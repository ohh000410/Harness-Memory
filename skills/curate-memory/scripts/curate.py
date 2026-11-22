"""Memory curation (curate-memory): code makes the edits, the LLM only returns JSON decisions.

usage: python -X utf8 curate.py [--only ROOT | --path DIR] [--config FILE] [--dry-run [--llm-dry]] [--no-llm] [--full] [--model MODEL]

- Targets: every root folder under the configured groups (default: main ~/.claude/projects/memories/<root>,
  agent ~/.agents/agent-memory/<root>). A root containing `.curate-ignore` is skipped.
- Only roots with work: files changed since the last run (checkpoint) or roots with audit findings.
- Code: loose leaves at the root → distilled/, index lines pointing to missing leaves removed, frontmatter name and
  description filled, [[links]] to removed leaves unwrapped, struck text deleted in superseded form, log.md rotated past
  64 KB, backups, log and verification.
- LLM (claude -p, no tools, no settings read, given only this root's content): where to route leaves and new indexes,
  contradiction pairs, shorter MEMORY.md hooks, split points of big leaves that cannot be split at headings. Results are
  JSON — code validates before applying; anything unusable is skipped and reported.
- Splitting: code cuts the original text verbatim and checks that the joined parts are byte-identical to the body.
- --dry-run: report the work without changing files (no LLM calls either; --llm-dry makes the calls).
- --path: that one folder only (test mode, checkpoint untouched). --only: the root with that name only (checkpoint untouched).
- Config: --config FILE, else env MEMORY_CURATION_CONFIG, else config.json in the repository root, else built-in defaults.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DEFAULTS = {
    "roots": {"main": "~/.claude/projects/memories", "agent": "~/.agents/agent-memory"},
    "logs": "~/.claude/logs/curate-memory",
    "claude": "claude",
    "model": "sonnet",
    "model_heavy": "opus",
    "archive_days": {"main": 60, "agent": 180},
}
# Project notes unchanged for longer than archive_days move to archive/ — out of the indexes, found only by search.
# Rules, references and user notes (feedback, reference, user) and notes linked from ## Hot stay however old.
# Agent roots are read less often, so they get longer by default. A group missing from the map uses this:
ARCHIVE_DAYS_DEFAULT = 60


class Config:
    """Settings from a JSON file (every key optional); missing keys take DEFAULTS"""

    def __init__(self, path=None):
        path = path or os.environ.get("MEMORY_CURATION_CONFIG") or (REPO / "config.json" if (REPO / "config.json").exists() else None)
        data = json.loads(Path(path).expanduser().read_text(encoding="utf-8-sig")) if path else {}
        c = {**DEFAULTS, **data}
        # archive_days merges per group; "roots" is taken whole — a config that names only "main" must not
        # silently add the default agent folder (a test run once curated real memory that way)
        c["archive_days"] = {**DEFAULTS["archive_days"], **data.get("archive_days", {})}
        self.source = path
        self.groups = {g: Path(p).expanduser() for g, p in c["roots"].items()}
        self.logs = Path(c["logs"]).expanduser()
        self.checkpoint = self.logs / "checkpoint.json"
        self.claude = c["claude"]
        self.model = c["model"]
        self.model_heavy = c["model_heavy"]
        self.archive_days = c["archive_days"]

    def days(self, group):
        return self.archive_days.get(group, ARCHIVE_DAYS_DEFAULT)

    def claude_exe(self):
        exe = shutil.which(os.path.expanduser(self.claude))
        if not exe:
            raise RuntimeError(f"claude command not found: '{self.claude}' (set \"claude\" in the config)")
        return exe


CFG = None


def configure(path=None):
    global CFG
    CFG = Config(path)
    return CFG


LIMIT = 8192                 # leaf size limit
# Below this many leaves a root has no indexes/ and routes from MEMORY.md under ## Leaves — a root of this size reads
# better without indexes, and a new index needs >=3 leaves on one topic, which a smaller root rarely has
SMALL = 15
L0_TOKENS, L0_LINKS = 800, 25
# An index linking more leaves than this, or whose linked leaves exceed CTX_CHARS in total, is split by topic — this is
# what a session reads when it opens an index, and what one consolidation run (consolidate.py --index) sends at once
IDX_LINES = 25
LOG_ROTATE = 64 * 1024
HOOK = 80
CTX_CHARS = 150_000          # max characters sent to the LLM at once (beyond this, candidate leaves get descriptions only)
TYPES = ("user", "feedback", "project", "reference")
STRUCK = re.compile(r"~~(?:(?!~~)[^\n]){1,2000}?~~")   # within one line — detection and the line-by-line fix must see the same spans
FENCE = re.compile(r"^\s*(```|~~~)")
WIKI =re.compile(r"\[\[([^\]|#]+)\]\]")
MDLINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
NOW = datetime.now()
STAMP = NOW.strftime("%Y%m%d-%H%M%S")
TODAY = NOW.strftime("%Y-%m-%d")


# ---------- files ----------
class Doc:
    """Text file read and written with its BOM and line endings preserved"""

    def __init__(self, path):
        self.path = Path(path)
        raw = self.path.read_bytes()
        self.bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw[3:].decode("utf-8") if self.bom else raw.decode("utf-8")
        self.crlf = "\r\n" in text
        self.text = text.replace("\r\n", "\n")
        self.orig = self.text

    def changed(self):
        return self.text != self.orig

    def data(self):
        t = self.text.replace("\n", "\r\n") if self.crlf else self.text
        return (b"\xef\xbb\xbf" if self.bom else b"") + t.encode("utf-8")


def keys(s):
    k = s.lower().replace("_", "-")
    if k.endswith(".md"):
        k = k[:-3]
    return {k, re.sub(r"^(?:%s)-" % "|".join(TYPES), "", k)}


def frontmatter(text):
    """(dict of fm lines, end of fm) — ({}, 0) when there is none"""
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, 0
    fm = {}
    for line in m.group(1).split("\n"):
        mm = re.match(r"([A-Za-z_]+):\s*(.*)", line)
        if mm:
            fm[mm.group(1)] = mm.group(2).strip()
    return fm, m.end()


def kebab(stem):
    return re.sub(r"[^\w-]+", "-", stem.lower().replace("_", "-")).strip("-")


def mask_code(text):
    """Blank out fenced blocks (``` and ~~~) and `inline code`, keeping line count, so ~~ inside code is not read as struck text"""
    out, fence = [], None
    for line in text.split("\n"):
        m = FENCE.match(line)
        if fence or m:
            if m and (fence is None or m.group(1) == fence):
                fence = None if fence else m.group(1)
            out.append("")
            continue
        out.append(re.sub(r"`[^`\n]*`", "", line))
    return "\n".join(out)


# ---------- root ----------
class Root:
    def __init__(self, group, path):
        self.group, self.path, self.name = group, Path(path), Path(path).name
        self.backups = {}      # relative path → backup path (before the first change in this run)
        self.writes = {}       # relative path → Doc
        self.actions = []      # report lines
        self.issues = []       # what could not be done
        self.created = []      # newly created files (relative paths)
        self.llm = []          # LLM call usage

    # files
    def rel(self, p):
        return Path(p).resolve().relative_to(self.path.resolve()).as_posix()

    def doc(self, rel):
        if rel not in self.writes:
            self.writes[rel] = Doc(self.path / rel)
        return self.writes[rel]

    def leaves(self):
        d = self.path / "distilled"
        return sorted(d.glob("*.md")) if d.exists() else []

    def archived(self):
        d = self.path / "archive"
        return sorted(d.glob("*.md")) if d.exists() else []

    def index_files(self):
        d = self.path / "indexes"
        return sorted(d.glob("*.md")) if d.exists() else []

    def routing_text(self):
        parts = []
        for p in [self.path / "MEMORY.md", *self.index_files()]:
            if p.exists():
                rel = self.rel(p)
                parts.append(self.writes[rel].text if rel in self.writes else Doc(p).text)
        return "\n".join(parts)


# ---------- audit ----------
def live_keys(roots):
    """Names (file and frontmatter name) of live leaves in every root — a link may point into another root"""
    out = set()
    for r in roots:
        for f in r.leaves() + r.archived():   # a [[link]] to an archived note is still a live link
            out |= keys(f.stem)
            fm, _ = frontmatter(Doc(f).text)
            if fm.get("name"):
                out |= keys(fm["name"])
    return out


def fingerprints(root):
    out = {}
    for p in root.path.rglob("*.md"):
        rel = root.rel(p)
        if re.match(r"(raw|\.archive|archive|indexes)/", rel) or rel in ("MEMORY.md", "log.md"):
            continue
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def recent_links(text):
    m = re.search(r"(?ms)^##\s+Recent / pending curate\s*$(.*?)(?=^##\s|\Z)", text)
    return [t for _, t in MDLINK.findall(m.group(1))] if m else []


def audit(root, live, previous):
    leaves = root.leaves()
    routing = root.routing_text()
    raw_names = {}
    for p in (root.path / "raw").rglob("*.md") if (root.path / "raw").exists() else []:
        for k in keys(p.stem):
            raw_names[k] = root.rel(p)
    a = {"unrouted": [], "big": [], "nofm": [], "dead": [], "struck": [], "pending": 0}
    routed = linked_names(routing)
    for f in leaves:
        t = Doc(f).text
        if STRUCK.search(mask_code(t)):
            a["struck"].append(f.name)
        if f.name not in routed:
            a["unrouted"].append(f.name)
        if f.stat().st_size > LIMIT:
            a["big"].append(f.name)
        fm, _ = frontmatter(t)
        need = ("name", "description")
        if not fm or any(not fm.get(n) for n in need):
            a["nofm"].append(f.name)
        for target in WIKI.findall(t):
            ks = keys(target.strip())
            if ks & live:
                continue
            if any(k in raw_names for k in ks):
                a["dead"].append((f.name, target.strip()))
            else:
                a["pending"] += 1
    mem = root.path / "MEMORY.md"
    mtext = Doc(mem).text if mem.exists() else ""
    a["recent"] = [Path(t.split("#")[0]).name for t in recent_links(mtext) if "distilled/" in t]   # a #section anchor is not part of the file name
    a["loose"] = [p.name for p in root.path.glob("*.md") if p.name not in ("MEMORY.md", "log.md")]
    a["dead_lines"] = dead_index_lines(root)
    # same L0 limits for every group
    a["l0_over"] = est_tokens(mtext) > L0_TOKENS or len(MDLINK.findall(mtext)) > L0_LINKS
    log = root.path / "log.md"
    a["log_big"] = log.exists() and log.stat().st_size > LOG_ROTATE
    hot = section_lines(mtext, "Hot")
    cutoff = time.time() - CFG.days(root.group) * 86400
    a["stale"] = [f.name for f in leaves if f.stat().st_mtime < cutoff
                  and re.search(r"(?m)^\s*type:\s*project\s*$", Doc(f).text[:frontmatter(Doc(f).text)[1]])
                  and not any(f"distilled/{f.name})" in l for l in hot)]
    big = []
    for p in root.index_files():
        names = [x for x in index_leaves(Doc(p).text) if x not in a["stale"]]   # size without notes about to be archived
        size = sum(len(Doc(root.path / "distilled" / n).text) for n in names if (root.path / "distilled" / n).exists())
        if len(names) > IDX_LINES or size > CTX_CHARS:
            big.append((len(names), size, p.name))
    a["big_idx"] = [n for *_, n in sorted(big, reverse=True)]   # biggest index first
    cur = fingerprints(root)
    a["delta"] = sorted(r for r in cur if previous.get(r) != cur[r])
    a["deleted"] = sorted(r for r in previous if r not in cur)
    a["fingerprints"] = cur
    return a


def linked_names(text):
    """File names that markdown links in the text point to (exact targets, not substrings: api.md is not api-v2.md)"""
    out = set()
    for _, target in MDLINK.findall(text):
        name = target.split("#")[0].rstrip("/").split("/")[-1]
        out.add(name if name.endswith(".md") else name + ".md")
    return out


def link_title(line, fallback):
    """Text of the first [title](...) on an index line — the line need not start with '- ['"""
    m = re.search(r"\[([^\]]*)\]\(", line)
    return m.group(1) if m else Path(fallback).stem


def est_tokens(text):
    """Token estimate: about 4 characters per token for ASCII, about one per character for other scripts (Hangul, CJK)"""
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return ascii_chars / 4 + (len(text) - ascii_chars)


def index_leaves(text):
    """Leaf names linked by the '- [..](../distilled/x.md)' lines of an index file (in line order)"""
    return [m.group(1) for l in text.split("\n") if l.lstrip().startswith("- ")
            for m in [re.search(r"\(\.\./distilled/([^)#]+)\)", l)] if m]


def dead_index_lines(root):
    """List lines in MEMORY.md and indexes that point to files that do not exist: (file, line)"""
    out = []
    for p in [root.path / "MEMORY.md", *root.index_files()]:
        if not p.exists():
            continue
        for line in Doc(p).text.split("\n"):
            if not line.lstrip().startswith("- "):
                continue
            for _, target in MDLINK.findall(line):
                if re.match(r"(https?:|mailto:|#)", target):
                    continue
                if not (p.parent / target.split("#")[0]).exists():
                    out.append((root.rel(p), line))
                    break
    return out


def needs_work(a, full):
    return full or bool(a["delta"] or a["deleted"] or a["unrouted"] or a["big"] or a["nofm"] or a["dead"]
                        or a["struck"] or a["recent"] or a["loose"] or a["dead_lines"] or a["l0_over"] or a["log_big"]
                        or a["big_idx"] or a["stale"])


# ---------- fixes made by code ----------
def backup(root, rel, action):
    if rel in root.backups:
        return
    src = root.path / rel
    dst = root.path / "raw/legacy" / f"{action}-{STAMP}" / rel
    root.backups[rel] = dst
    if src.exists():
        root.pending_backup.append((src, dst))


def fix_layout(root, a):
    for name in a["loose"]:
        src = root.path / name
        dst = root.path / "distilled" / name
        if dst.exists():
            root.issues.append(f"layout: distilled/{name} already exists — left {name} in place")
            continue
        root.moves.append((src, dst))
        for p in [root.path / "MEMORY.md", *root.index_files()]:
            if p.exists():
                d = root.doc(root.rel(p))
                new = re.sub(r"\]\((?:\./)?%s\)" % re.escape(name), "](%s)" % ("distilled/" + name if p.name == "MEMORY.md" else "../distilled/" + name), d.text)
                if new != d.text:
                    backup(root, root.rel(p), "layout")
                    d.text = new
        root.actions.append(f"layout: {name} → distilled/")


def fix_dead_lines(root, a):
    for rel, line in a["dead_lines"]:
        d = root.doc(rel)
        if line in d.text.split("\n"):
            backup(root, rel, "routing")
            d.text = "\n".join(l for l in d.text.split("\n") if l != line)
            root.actions.append(f"sync: removed line for a missing leaf from {rel}")


def fix_frontmatter(root, a):
    for name in a["nofm"]:
        rel = f"distilled/{name}"
        d = root.doc(rel)
        fm, end = frontmatter(d.text)
        slug = kebab(Path(name).stem)
        body = d.text[end:]
        if not fm.get("description"):
            h = re.search(r"^#+\s+(.+)$", body, re.M)
            first = h.group(1) if h else next((l.strip() for l in body.split("\n") if l.strip() and not l.startswith("---")), slug)
            desc = re.split(r"(?<=[.!?。])\s", first)[0][:150].replace('"', "'")
        else:
            desc = None
        if not end:
            lines = [f"name: {slug}"] + ([f'description: "{desc}"'] if desc else [])
            new = "---\n" + "\n".join(lines) + "\n---\n\n" + d.text
        else:
            block = d.text[4:end - 4]
            if not fm.get("name"):
                block = re.sub(r"(?m)^name:.*\n?", "", block)
                block = f"name: {slug}\n" + block
            if desc:
                block = re.sub(r"(?m)^description:.*\n?", "", block)
                block = block.rstrip("\n") + f'\ndescription: "{desc}"'
            new = "---\n" + block.strip("\n") + "\n---\n" + d.text[end:]
        backup(root, rel, "frontmatter")
        d.text = new
        root.actions.append(f"frontmatter: {name}")


def fix_dead_links(root, a):
    for name, target in a["dead"]:
        rel = f"distilled/{name}"
        d = root.doc(rel)
        new = d.text.replace(f"[[{target}]]", target)
        if new != d.text:
            backup(root, rel, "deadlink")
            d.text = new
            root.actions.append(f"deadlink: {name} unwrapped [[{target}]]")


def fix_struck(root, a):
    for name in a["struck"]:
        rel = f"distilled/{name}"
        d = root.doc(rel)
        backup(root, rel, "superseded")   # if an earlier step already backed it up, this points to that copy
        dest = root.backups[rel].relative_to(root.path).as_posix()
        out = []
        for line, plain in zip(d.text.split("\n"), mask_code(d.text).split("\n")):
            if not STRUCK.search(plain):
                out.append(line)
                continue
            # remove struck spans outside `inline code` only — code on the same line stays as written
            line = "".join(part if part.startswith("`") else STRUCK.sub("", part) for part in re.split(r"(`[^`\n]*`)", line))
            line = re.sub(r"^(\s*(?:[-*]\s+)?(?:[^:\n]{0,40}:\s*)?)\s*(?:→|->|—|–)\s*", r"\1", line)   # arrow left where the struck text was
            line = re.sub(r"\s{2,}", " ", line).rstrip()
            m = re.search(r"\((\d{4}-\d{2}-\d{2})\s*superseded:([^)]*)\)", line)
            if m and "previous →" not in m.group(2):
                line = line[:m.end() - 1] + f", previous → {dest})" + line[m.end():]
            elif not m:
                line += f" ({TODAY} superseded: struck text replaced, previous → {dest})"
            out.append(line)
        d.text = "\n".join(out)
        root.actions.append(f"superseded: {name} struck text removed")


def fix_log(root, a):
    if not a["log_big"]:
        return
    d = root.doc("log.md")
    lines = d.text.rstrip("\n").split("\n")
    keep, old = lines[-30:], lines[:-30]
    root.new_files.append((root.path / f"raw/legacy/log-rotated-{STAMP}.md", "\n".join(old) + "\n"))
    backup(root, "log.md", "logrotate")
    d.text = "\n".join(keep) + "\n"
    root.actions.append(f"log: {len(old)} lines to raw/legacy")


# ---------- LLM ----------
SYSTEM = ("You maintain one person's markdown memory folder. You only return JSON decisions; code applies them. "
          "Use only the material in the message. Keep the notes' language as-is (do not translate). "
          "Output one JSON object and nothing else.")


def call_llm(root, model, prompt, effort="medium"):
    exe = CFG.claude_exe()
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [exe, "-p", "--model", model, "--effort", effort, "--tools", "", "--setting-sources", "",
               "--system-prompt", SYSTEM, "--output-format", "json", "--no-session-persistence"]
        t0 = time.time()
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", cwd=tmp, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"claude exit {r.returncode}: {r.stderr[-300:]}")
    out = json.loads(r.stdout)
    u = out.get("usage", {})
    root.llm.append({"model": model, "sec": round(time.time() - t0), "cost": out.get("total_cost_usd", 0),
                     "in": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0),
                     "out": u.get("output_tokens", 0)})
    text = out.get("result", "")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise RuntimeError("no JSON in the reply")
    return json.loads(m.group(0))


def leaf_text(root, name):
    """distilled/<name> — a note due to move from the root is still in its old place (all moves happen at the end)"""
    p = root.path / "distilled" / name
    return Doc(p if p.exists() else root.path / name).text


def leaf_brief(f):
    t = Doc(f).text
    fm, _ = frontmatter(t)
    return f"{f.name} — {fm.get('description', '')[:160]}"


def is_small(root):
    """Root routed from MEMORY.md ## Leaves without indexes — under SMALL leaves and no index yet. Once a root has indexes it keeps them"""
    return len(root.leaves()) < SMALL and not root.index_files()


def structural_prompt(root, a, to_route, delta_leaves, candidates):
    mem = root.doc("MEMORY.md").text if (root.path / "MEMORY.md").exists() else ""
    idx = {p.name: root.doc(root.rel(p)).text for p in root.index_files()}
    small = is_small(root)
    parts = [f"# Root: {root.group} memory, {len(root.leaves())} leaves", "", "## MEMORY.md", mem, ""]
    for n, t in idx.items():
        parts += [f"## indexes/{n}", t, ""]
    parts += ["## Leaves to route (full text)"]
    for name in to_route:
        parts += [f"### {name}", leaf_text(root, name)[:6000], ""]
    budget = CTX_CHARS - sum(len(p) for p in parts)
    parts += ["## Changed leaves (check contradictions with the candidates; check their current index lines below)"]
    for name in delta_leaves:
        t = leaf_text(root, name)
        lines_now = [l.strip() for l in (mem + "\n" + "\n".join(idx.values())).split("\n") if f"distilled/{name})" in l]
        parts += [f"### {name}", "current index lines: " + (" | ".join(lines_now) or "(none)"), t[:8000], ""]
        budget -= len(t)
    parts += ["## Candidate leaves to compare (same topic)"]
    for f in candidates:
        t = Doc(f).text
        if budget > len(t):
            parts += [f"### {f.name}", t, ""]
            budget -= len(t)
        else:
            parts += [f"- {leaf_brief(f)}"]
    if a.get("regroup"):
        how = (f"This root has {len(root.leaves())} leaves and no topic index yet: put EVERY leaf listed into NEW:<kebab-topic> "
               "topics, each with at least 3 leaves, at most 7 topics. Leaves already under \"## Leaves\" move too. "
               "Group by what a session would come looking for: keep lasting knowledge (rules, procedures, contacts, vendors, "
               "reference facts) apart from ongoing work items (a purchase, a task, a project in progress).")
    elif small:
        how = f"This root has fewer than {SMALL} leaves: use \"LEAVES\" (routed straight from MEMORY.md)."
    else:
        how = "Use the closest existing index. NEW only when ≥3 leaves (counting this batch) share a topic no index covers."
    rules = f"""
## Task
Return JSON: {{"route": [...], "contradictions": [...], "hook_updates": [...], "l0_hooks": [...]}}.
0. hook_updates: for a changed leaf whose current index line hook no longer matches its content (stale status, wrong values): {{"leaf": "<file>.md", "hook": "<≤{HOOK} chars>"}}. Only when clearly stale; empty list otherwise.
1. route: one item per leaf in "Leaves to route": {{"leaf": "<file>.md", "index": "<existing index file name>" or "NEW:<kebab-topic>" {'or "LEAVES"' if small else ''}, "title": "<short link title>", "hook": "<≤{HOOK} chars, what is in the leaf>"}}.
   {how}
2. contradictions: pairs where a changed leaf states a fact that conflicts with a candidate leaf (same subject, incompatible values/claims): {{"a": "<file>.md", "b": "<file>.md", "why": "<one line>"}}. Do not pick a winner. Empty list if none.
3. l0_hooks: {"MEMORY.md is over its limit — give shorter hooks for its link lines: {\"target\": \"<link target as written>\", \"hook\": \"<shorter>\"}" if a["l0_over"] else "empty list"}.
"""
    return "\n".join(parts) + rules


def split_prompt(name, body_lines):
    numbered = "\n".join(f"{i + 1:04d}| {l}" for i, l in enumerate(body_lines))
    return (f"The note below is over {LIMIT} bytes. Choose where to split it into parts by meaning, each part under about "
            f"{LIMIT - 1200} bytes. Never split inside a table, code block, or numbered procedure.\n"
            f'Return JSON: {{"starts": [<line numbers where part 2, 3, ... begin>]}} (line 1 always starts part 1).\n\n'
            f"## {name}\n{numbered}")


def apply_routing(root, a, decision, to_route):
    small = is_small(root)
    existing = {p.name for p in root.index_files()}
    items = [r for r in decision.get("route", []) if r.get("leaf") in to_route]
    new_counts = {}
    for r in items:
        if str(r.get("index", "")).startswith("NEW:"):
            new_counts[r["index"]] = new_counts.get(r["index"], 0) + 1
    mem = root.doc("MEMORY.md")
    made = {}   # index created in this run → titles of its leaves (the index line describes the grouped titles, not the first leaf's hook)
    for r in items:
        leaf, idx, title = r["leaf"], str(r.get("index", "")).strip(), (r.get("title") or Path(r["leaf"]).stem).strip()
        # spelling variants of an index name ("project", "indexes/project.md") mean the same index
        if idx not in ("LEAVES",) and not idx.startswith("NEW:"):
            idx = idx.removeprefix("indexes/")
            idx = idx if idx.endswith(".md") else idx + ".md"
        hook = (r.get("hook") or "").strip().replace("\n", " ")[:HOOK]
        if idx == "LEAVES" and small:
            line = f"- [{title}](distilled/{leaf}) — {hook}"
            if "## Leaves" not in mem.text:
                mem.text = re.sub(r"(?m)^## Recent / pending curate", "## Leaves\n\n## Recent / pending curate", mem.text) \
                    if "## Recent / pending curate" in mem.text else mem.text.rstrip("\n") + "\n\n## Leaves\n"
            backup(root, "MEMORY.md", "routing")
            if f"distilled/{leaf})" not in "\n".join(section_lines(mem.text, "Leaves")):   # a note re-listed under Recent is already here
                # the line is inserted as text: a function replacement keeps backslashes (C:\Users\...) out of re's template syntax
                mem.text = re.sub(r"(?m)^## Leaves\n", lambda m: f"## Leaves\n{line}\n", mem.text, count=1)
        elif idx in existing or (idx.startswith("NEW:") and new_counts.get(idx, 0) >= 3 and not small):
            fname = idx if idx in existing else kebab(idx[4:]) + ".md"
            rel = f"indexes/{fname}"
            if not (root.path / rel).exists() and rel not in root.writes:
                root.new_files.append((root.path / rel, f"# Index: {kebab(idx[4:])}\n\n"))
                root.writes[rel] = Doc.__new__(Doc)
                d = root.writes[rel]
                d.path, d.bom, d.crlf, d.text, d.orig = root.path / rel, False, False, f"# Index: {kebab(idx[4:])}\n\n", ""
                topic = f"- [{kebab(idx[4:])}](indexes/{fname}) — {hook}"
                made[fname] = []
                backup(root, "MEMORY.md", "routing")
                if "## Topic indexes" in mem.text:
                    mem.text = re.sub(r"(?m)^## Topic indexes\n", lambda m: f"## Topic indexes\n{topic}\n", mem.text, count=1)
                else:
                    mem.text = mem.text.rstrip("\n") + f"\n\n## Topic indexes\n{topic}\n"
                root.created.append(rel)
            d = root.doc(rel)
            if f"../distilled/{leaf})" not in d.text:   # a note re-listed under Recent may already be in this index
                backup(root, rel, "routing")
                d.text = d.text.rstrip("\n") + f"\n- [{title}](../distilled/{leaf}) — {hook}\n"
            if fname in made:
                made[fname].append(title)
        else:
            root.issues.append(f"route: {leaf} → '{idx}' not usable (no such index, or the new-index condition is not met)")
            continue
        # Drop from Recent. On the night a root moves to indexes, also drop its line under ## Leaves (the leaf is now in an index).
        # Only within that section — a plain text match would also remove the same line under ## Hot
        sections = ["Recent / pending curate"] + (["Leaves"] if a.get("regroup") else [])
        for sec in sections:
            new = drop_in_section(mem.text, sec, f"distilled/{leaf})")
            if new != mem.text:
                backup(root, "MEMORY.md", "routing")
                mem.text = new
        root.actions.append(f"route: {leaf} → {idx}")
    # remove a ## Leaves heading left empty after everything moved
    if a.get("regroup") and not [l for l in section_lines(mem.text, "Leaves") if l]:
        mem.text = re.sub(r"(?m)^## Leaves\n+(?=## |\Z)", "", mem.text)
    # new index line description = titles of its leaves. Once an index exists, drop the "- None." placeholder
    for fname, titles in made.items():
        desc = ", ".join(titles)
        desc = desc if len(desc) <= HOOK else desc[:HOOK - 1] + "…"
        mem.text = re.sub(r"(?m)^(- \[[^\]]*\]\(indexes/%s\) — ).*$" % re.escape(fname), lambda m: m.group(1) + desc, mem.text)
    if made:
        mem.text = re.sub(r"(?ms)(^## Topic indexes\n(?:- .*\n)*?)- None\.\n", r"\1", mem.text)


def drop_in_section(text, heading, target):
    """Remove only the '- ' lines pointing to target inside the ## heading section (other sections untouched)"""
    m = re.search(r"(?ms)^##\s+%s\s*$(.*?)(?=^##\s|\Z)" % re.escape(heading), text)
    if not m:
        return text
    body = "\n".join(l for l in m.group(1).split("\n") if not (l.lstrip().startswith("- ") and target in l))
    return text[:m.start(1)] + body + text[m.end(1):]


def section_lines(text, heading):
    m = re.search(r"(?ms)^##\s+%s\s*$(.*?)(?=^##\s|\Z)" % re.escape(heading), text)
    return {l.strip() for l in m.group(1).split("\n")} if m else set()


def apply_contradictions(root, decision):
    names = {f.name for f in root.leaves()}
    for c in decision.get("contradictions", []):
        a, b = c.get("a"), c.get("b")
        if a not in names or b not in names or a == b:
            root.issues.append(f"contradiction: {a}↔{b} not usable")
            continue
        for x, y in ((a, b), (b, a)):
            rel = f"distilled/{x}"
            d = root.doc(rel)
            fm, _ = frontmatter(Doc(root.path / "distilled" / y).text)
            ref = fm.get("name") or kebab(Path(y).stem)
            if f"[[{ref}]] (contradicts)" in d.text:
                continue
            backup(root, rel, "contradiction")
            d.text = d.text.rstrip("\n") + f"\n\n[[{ref}]] (contradicts)\n"
        root.actions.append(f"contradiction: {a} ↔ {b} — {c.get('why', '')[:80]}")


def apply_hook_updates(root, decision, delta_leaves):
    for h in decision.get("hook_updates", []):
        leaf, hook = h.get("leaf"), (h.get("hook") or "").strip().replace("\n", " ")[:HOOK]
        if leaf not in delta_leaves or not hook:
            continue
        for p in [root.path / "MEMORY.md", *root.index_files()]:
            if not p.exists():
                continue
            r = root.rel(p)
            d = root.doc(r)
            new = re.sub(r"(?m)^(\s*-\s*\[[^\]]*\]\([^)]*distilled/%s\))\s*—.*$" % re.escape(leaf),
                         lambda m: f"{m.group(1)} — {hook}", d.text)
            if new != d.text:
                backup(root, r, "routing")
                d.text = new
        root.actions.append(f"hook: {leaf} → {hook}")


def apply_l0_hooks(root, decision):
    mem = root.doc("MEMORY.md")
    for h in decision.get("l0_hooks", []):
        target, hook = h.get("target"), (h.get("hook") or "").strip().replace("\n", " ")[:HOOK]
        if not target or not hook:
            continue
        new = re.sub(r"(?m)^(\s*-\s*\[[^\]]*\]\(%s\))\s*—.*$" % re.escape(target), lambda m: f"{m.group(1)} — {hook}", mem.text)
        if new != mem.text:
            backup(root, "MEMORY.md", "routing")
            mem.text = new
    if decision.get("l0_hooks"):
        root.actions.append("l0: hooks shortened")


# ---------- splitting ----------
def in_block(lines, i):
    """Whether cutting before line i lands inside a table or code block"""
    open_fence = None   # ``` or ~~~ block still open before line i
    for l in lines[:i]:
        m = FENCE.match(l)
        if m and (open_fence is None or m.group(1) == open_fence):
            open_fence = None if open_fence else m.group(1)
    fence = open_fence is not None
    table = i > 0 and lines[i - 1].lstrip().startswith("|") and lines[i].lstrip().startswith("|")
    return fence or table


def heading_starts(lines, room):
    """Cut at ## headings — list of start lines if every part fits in room bytes, else None"""
    heads = [i for i, l in enumerate(lines) if re.match(r"##+\s", l) and i > 0 and not in_block(lines, i)]
    starts, size, last = [], 0, 0
    for i in range(len(lines)):
        size += len(lines[i].encode("utf-8")) + 1
        if i + 1 < len(lines) and (i + 1) in heads and size > room * 0.5:
            starts.append(i + 1)
            size = 0
    chunks = [lines[s:e] for s, e in zip([0] + starts, starts + [len(lines)])]
    if any(len("\n".join(c).encode("utf-8")) > room for c in chunks):
        return None
    return starts or None


def shrink(root, name, model, use_llm):
    rel = f"distilled/{name}"
    d = root.doc(rel)
    fm, end = frontmatter(d.text)
    head, body = d.text[:end], d.text[end:]
    lines = body.split("\n")
    room = LIMIT - len(head.encode("utf-8")) - 400
    starts = heading_starts(lines, room)
    how = "heading"
    if not starts:
        if not use_llm:
            root.issues.append(f"shrink: {name} cannot be split at headings (run without LLM)")
            return
        try:
            starts = sorted({int(s) - 1 for s in call_llm(root, model, split_prompt(name, lines), "high").get("starts", [])})
        except Exception as e:
            root.llm_failed = True
            root.issues.append(f"shrink: {name} LLM failed — {e}")
            return
        how = "LLM"
    if not starts or starts[0] <= 0 or starts[-1] >= len(lines) or any(in_block(lines, s) for s in starts):
        root.issues.append(f"shrink: {name} split positions invalid {starts}")
        return
    chunks = ["\n".join(lines[s:e]) for s, e in zip([0] + starts, starts + [len(lines)])]
    if "\n".join(chunks) != body:
        root.issues.append(f"shrink: {name} joined parts differ from the original body — skipped")
        return
    stem, slug = Path(name).stem, fm.get("name") or kebab(Path(name).stem)
    # part numbers start at the next free number — a note split before may grow again while its -2 part already exists
    used = {p.name for p in (root.path / "distilled").glob(f"{stem}-*.md")}
    parts, n = [], 2
    for c in chunks[1:]:
        while f"{stem}-{n}.md" in used:
            n += 1
        parts.append((f"{stem}-{n}.md", f"{slug}-{n}", c, n))
        n += 1
    total = len(chunks)
    # build and check every part (and the shortened original) before queuing any write: all parts or none
    texts = []
    for pname, pslug, c, num in parts:
        fm_lines = head.strip("\n").split("\n")[1:-1] if head else []
        fm_lines = [l for l in fm_lines if not re.match(r"(name|description):", l)]
        desc = (fm.get("description", "").strip('"') + f" ({num})").replace('"', "'")
        texts.append((pname, "---\nname: " + pslug + f'\ndescription: "{desc}"\n' + "\n".join(fm_lines) + "\n---\n"
                      + c.rstrip("\n") + f"\n\n[[{slug}]] (refines)\n"))
    first = head + chunks[0].rstrip("\n") + "\n\n" + "\n".join(f"[[{p[1]}]] (related)" for p in parts) + "\n"
    if any(len(t.encode("utf-8")) > LIMIT for t in [first] + [t for _, t in texts]):
        root.issues.append(f"shrink: {name} a part is still over {LIMIT} bytes — skipped")
        return
    for pname, text in texts:
        root.new_files.append((root.path / "distilled" / pname, text))
        root.created.append(f"distilled/{pname}")
    backup(root, rel, "shrink")
    d.text = first
    # part lines go right after the index line of the original
    for p in [root.path / "MEMORY.md", *root.index_files()]:
        if not p.exists():
            continue
        r = root.rel(p)
        di = root.doc(r)
        out = []
        for l in di.text.split("\n"):
            out.append(l)
            if l.lstrip().startswith("- ") and f"distilled/{name})" in l:
                for pname, _, _, num in parts:
                    out.append(re.sub(r"\]\(([^)]*?)%s\)" % re.escape(name), "](\\g<1>%s)" % pname,
                                      re.sub(r"^(\s*-\s*\[)([^\]]*)\]", lambda m: f"{m.group(1)}{m.group(2)} {num}]", l)))
        if "\n".join(out) != di.text:
            backup(root, r, "shrink")
            di.text = "\n".join(out)
    root.actions.append(f"shrink({how}): {name} {len(Doc(root.path / rel).orig.encode('utf-8'))}B → {total} parts")


def archive_stale(root, a):
    """Move long-unchanged project notes to archive/ and drop their lines from MEMORY.md and indexes (notes are kept, found by search)"""
    taken = [n for n in a["stale"] if (root.path / "archive" / n).exists()]
    for n in taken:   # an archived note of the same name is never overwritten — this one stays active until renamed
        root.issues.append(f"archive: {n} not moved — archive/{n} already exists (rename one of them)")
    a["stale"] = [n for n in a["stale"] if n not in taken]
    if not a["stale"]:
        return
    for p in [root.path / "MEMORY.md", *root.index_files()]:
        if not p.exists():
            continue
        rel = root.rel(p)
        d = root.doc(rel)
        keep = [l for l in d.text.split("\n")
                if not (l.lstrip().startswith("- ") and any(f"distilled/{n})" in l for n in a["stale"]))]
        if "\n".join(keep) != d.text:
            backup(root, rel, "archive")
            d.text = "\n".join(keep)
    for n in a["stale"]:
        root.moves.append((root.path / "distilled" / n, root.path / "archive" / n))
    root.actions.append(f"archive: {len(a['stale'])} project notes (unchanged for over {CFG.days(root.group)} days) → archive/")


def split_index(root, iname, model):
    """Split one big index into several smaller topic indexes. The LLM sees only index lines and leaf descriptions and picks
    the groups; code moves the lines verbatim"""
    rel = f"indexes/{iname}"
    d = root.doc(rel)
    lines = {}
    for l in d.text.split("\n"):
        m = re.search(r"\(\.\./distilled/([^)#]+)\)", l)
        if l.lstrip().startswith("- ") and m and m.group(1) not in lines:
            lines[m.group(1)] = l
    briefs = [f"- {n} | {lines[n].strip()[2:][:200]} | {leaf_brief(root.path / 'distilled' / n).split(' — ', 1)[-1][:200]}"
              for n in lines if (root.path / "distilled" / n).exists()]
    prompt = (f"# Index {iname} in memory {root.name}: {len(briefs)} notes — too many for one index\n\n" + "\n".join(briefs) +
              f"\n\n## Task\nSplit these notes into smaller topic indexes. Return JSON: {{\"groups\": [{{\"name\": \"<kebab-topic>\", "
              f"\"leaves\": [\"<file>.md\", ...]}}]}}. Every note exactly once. Each group 3 to {IDX_LINES} notes, as few groups as that allows. "
              "Group by what a session would come looking for; keep lasting knowledge (rules, procedures, contacts, vendors, reference facts) "
              "apart from ongoing work items. Names must differ from: " + ", ".join(p.stem for p in root.index_files()))
    try:
        groups = call_llm(root, model, prompt).get("groups", [])
    except Exception as e:
        root.llm_failed = True
        root.issues.append(f"split-index: {iname} LLM failed — {e}")
        return
    assigned = [x for g in groups for x in g.get("leaves", [])]
    names = [kebab(str(g.get("name", ""))) for g in groups]
    existing = {p.stem for p in root.index_files()}
    sizes = [sum(len(Doc(root.path / "distilled" / x).text) for x in g.get("leaves", []) if (root.path / "distilled" / x).exists()) for g in groups]
    why = []
    if sorted(assigned) != sorted(lines):
        why.append(f"leaves missing or duplicated ({len(assigned)}/{len(lines)})")
    if len(groups) < 2 or any(not 1 <= len(g.get("leaves", [])) <= IDX_LINES for g in groups):
        why.append(f"group sizes {[len(g.get('leaves', [])) for g in groups]}")
    if len(set(names)) != len(names) or any(not n or n in existing for n in names):
        why.append(f"names {names}")
    if any(s > CTX_CHARS for s in sizes):
        why.append(f"character total {max(sizes):,} > {CTX_CHARS:,}")
    if why:
        root.issues.append(f"split-index: {iname} skipped — " + "; ".join(why))
        return
    mem = root.doc("MEMORY.md")
    backup(root, "MEMORY.md", "split-index")
    new_lines = []
    for name, g in zip(names, groups):
        body = "\n".join(lines[x] for x in lines if x in g["leaves"])   # original order, original lines
        root.new_files.append((root.path / f"indexes/{name}.md", f"# Index: {name}\n\n{body}\n"))
        root.created.append(f"indexes/{name}.md")
        titles = ", ".join(link_title(lines[x], x) for x in lines if x in g["leaves"])
        new_lines.append(f"- [{name}](indexes/{name}.md) — " + (titles if len(titles) <= HOOK else titles[:HOOK - 1] + "…"))
    # the old index goes to raw/legacy (with this run's changes), and the new index lines replace its line in MEMORY.md
    root.new_files.append((root.path / f"raw/legacy/split-index-{STAMP}/{rel}", d.text))
    root.moves.append((root.path / rel, root.path / f"raw/legacy/split-index-{STAMP}/{rel}.orig"))
    root.writes.pop(rel, None)
    mem.text = place_split_lines(mem.text, iname, new_lines)
    root.actions.append(f"split-index: {iname} {len(lines)} lines → {len(groups)} indexes ({', '.join(names)})")


def place_split_lines(text, iname, new_lines):
    """Remove every line pointing to the old index (it may appear in several sections such as Hot) and put the new
    index lines under ## Topic indexes — replacing only the first match could leave a dead line in Topic indexes"""
    pos = text.find("## Topic indexes")
    slot = text.count("\n", 0, pos) if pos >= 0 else None
    pat = re.compile(r"^\s*- \[[^\]]*\]\(indexes/%s\).*$" % re.escape(iname))
    lines = text.split("\n")
    first = next((i for i, l in enumerate(lines) if pat.match(l) and slot is not None and i > slot
                  and not any(lines[j].startswith("## ") for j in range(slot + 1, i))), None)
    out = []
    for i, l in enumerate(lines):
        if pat.match(l):
            if i == first:
                out.extend(new_lines)   # its original place inside Topic indexes
            continue
        out.append(l)
    text = "\n".join(out)
    if first is None:
        text = re.sub(r"(?m)^## Topic indexes\n(- None\.\n)?", lambda m: "## Topic indexes\n" + "\n".join(new_lines) + "\n", text, count=1) \
            if "## Topic indexes" in text else text.rstrip("\n") + "\n\n## Topic indexes\n" + "\n".join(new_lines) + "\n"
    return text


# ---------- run ----------
def write_all(root, dry):
    """Backups → moves → new files → changed files. Writes nothing when dry.
    First checks that nothing it is about to change was changed on disk since this run read it — a live session may have saved
    a note while the run waited for the LLM. On any such change the whole root is left untouched and retried next run."""
    if dry:
        return
    conflicts = [rel for rel, d in root.writes.items()
                 if rel not in root.created and d.changed() and d.path.exists() and Doc(d.path).text != d.orig]
    conflicts += [root.rel(src) for src, _ in root.moves if not Path(src).exists()]
    conflicts += [root.rel(dst) for _, dst in root.moves if Path(dst).exists()]
    conflicts += [root.rel(p) for p, _ in root.new_files if Path(p).exists()]
    if conflicts:
        root.write_conflict = True
        root.issues.append(f"write: files changed during the run, nothing written (retried next run): {conflicts[:3]}")
        return
    for src, dst in root.pending_backup:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    for src, dst in root.moves:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, dst)
        for d in root.writes.values():   # an edit made this run to a moved note (e.g. frontmatter) is written at its new place
            if Path(d.path) == Path(src):
                d.path = Path(dst)
    for p, text in root.new_files:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    for rel, d in root.writes.items():
        if rel in root.created or not d.changed():   # files created this run are written below (an existing empty file is not "created")
            continue
        note = Path(d.path).parent.name in ("distilled", "archive")
        mtime = Path(d.path).stat().st_mtime if note else None
        d.path.write_bytes(d.data())
        if note:   # housekeeping edits do not make a note "recent" — its age (archive rule) stays the author's last change
            os.utime(d.path, (time.time(), mtime))
    for rel in root.created:
        d = root.writes.get(rel)
        if d is not None and d.orig == "":
            d.path.write_bytes(d.text.encode("utf-8"))


def finish(root, dry):
    if not root.actions or dry or not (root.path / "MEMORY.md").exists():
        return
    mem = root.doc("MEMORY.md")
    if re.search(r"(?m)^Last curated: .*$", mem.text):
        mem.text = re.sub(r"(?m)^Last curated: .*$", f"Last curated: {TODAY} by curate-memory.", mem.text)
    else:
        mem.text = re.sub(r"(?m)^(# .*\n)", f"\\1\nLast curated: {TODAY} by curate-memory.\n", mem.text, count=1)
    log = root.doc("log.md") if (root.path / "log.md").exists() else None
    summary = "; ".join(sorted({a.split(":")[0] for a in root.actions}))
    line = f"- [{TODAY}] curated | routing — {summary} ({len(root.actions)} actions)"
    if log is not None:
        log.text = log.text.rstrip("\n") + "\n" + line + "\n"
    else:
        root.new_files.append((root.path / "log.md", line + "\n"))


def run_root(root, a, args, model):
    root.pending_backup, root.moves, root.new_files = [], [], []
    fix_layout(root, a)
    fix_dead_lines(root, a)
    fix_frontmatter(root, a)
    fix_dead_links(root, a)
    fix_struck(root, a)
    fix_log(root, a)
    archive_stale(root, a)
    use_llm = not args.no_llm and (not args.dry_run or args.llm_dry)
    to_route = sorted((set(a["unrouted"]) | set(a["recent"]) | set(a["loose"])) - set(a["stale"]))
    # past SMALL with no index: on that one night, group every leaf (including those under ## Leaves) into topic indexes
    a["regroup"] = len(root.leaves()) >= SMALL and not root.index_files()
    if a["regroup"]:
        to_route = sorted(f.name for f in root.leaves() if f.name not in a["stale"])
    delta_leaves = sorted({Path(r).name for r in a["delta"] if r.startswith("distilled/")})
    if (to_route or delta_leaves or a["l0_over"]) and use_llm:
        cand = [f for f in root.leaves() if f.name not in delta_leaves]
        if root.index_files() and delta_leaves:   # only leaves in the same index
            routing_by_index = {p: Doc(p).text for p in root.index_files()}
            near = {f for p, t in routing_by_index.items() if any(d in t for d in delta_leaves) for f in root.leaves() if f.name in t}
            cand = [f for f in cand if f in near] or cand
        try:
            dec = call_llm(root, model, structural_prompt(root, a, to_route, delta_leaves, cand))
            apply_routing(root, a, dec, to_route)
            apply_hook_updates(root, dec, delta_leaves)
            apply_contradictions(root, dec)
            if a["l0_over"]:
                apply_l0_hooks(root, dec)
        except Exception as e:
            root.llm_failed = True
            root.issues.append(f"structural LLM: {e}")
    elif to_route:
        root.issues.append(f"route: {len(to_route)} leaves skipped (run without LLM)")
    for name in [n for n in a["big"] if n not in a["stale"]][:3]:   # a note going to archive/ is not split
        shrink(root, name, CFG.model_heavy, use_llm)
    # one big index per night
    if a["big_idx"]:
        if use_llm:
            split_index(root, a["big_idx"][0], model)
        else:
            root.issues.append(f"split-index: {a['big_idx'][0]} skipped (run without LLM)")
    finish(root, args.dry_run)
    write_all(root, args.dry_run)


def verify(root, before, live, dry):
    if dry:
        return []
    after = audit(root, live, {})
    problems = []
    if len(root.leaves()) + len(root.archived()) < before:   # archiving does not count as a loss
        problems.append(f"leaf count dropped {before}→{len(root.leaves()) + len(root.archived())}")
    for k in ("unrouted", "nofm", "dead", "struck", "dead_lines", "loose"):
        if after[k]:
            problems.append(f"remaining {k}: {after[k][:3]}")
    if getattr(root, "llm_failed", False):   # decisions that were due were not made — keep the checkpoint so they are asked again
        problems.append("an LLM call failed")
    if getattr(root, "write_conflict", False):
        problems.append("files changed during the run — nothing written")
    return problems


def main():
    ap = argparse.ArgumentParser(description="Curate file-based memory roots (see the module docstring).")
    ap.add_argument("--only", help="only the root with this folder name (checkpoint untouched)")
    ap.add_argument("--path", help="only this folder, as a main root (test mode, checkpoint untouched)")
    ap.add_argument("--config", help="JSON config file (default: env MEMORY_CURATION_CONFIG, else config.json in the repository root)")
    ap.add_argument("--dry-run", action="store_true", help="report the work without changing files or calling the LLM")
    ap.add_argument("--llm-dry", action="store_true", help="with --dry-run, still call the LLM and report its decisions")
    ap.add_argument("--no-llm", action="store_true", help="code fixes only")
    ap.add_argument("--full", action="store_true", help="process every root, not only those with changes or findings")
    ap.add_argument("--model", help="model for routing and index splits (default: config \"model\")")
    args = ap.parse_args()
    configure(args.config)
    model = args.model or CFG.model

    if args.path:
        roots = [Root("main", Path(args.path))]
        all_roots = roots
    else:
        all_roots = [Root(g, p) for g, base in CFG.groups.items() if base.exists()
                     for p in sorted(base.iterdir()) if p.is_dir() and not p.name.startswith(".") and not (p / ".curate-ignore").exists()]
        roots = [r for r in all_roots if not args.only or r.name == args.only]
    cp = json.loads(CFG.checkpoint.read_text(encoding="utf-8-sig")) if CFG.checkpoint.exists() and not args.path else {}
    prev_all = cp.get("roots", {})
    live = live_keys(all_roots)

    # a --path test writes its report next to that folder, not among the nightly reports
    out_dir = (Path(args.path).parent if args.path else CFG.logs) / f"curate-report-{NOW:%Y-%m-%d_%H%M%S}"
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"# Curate Memory {NOW:%Y-%m-%d %H:%M} [{'dry-run' if args.dry_run else 'live'}] curate.py", ""]
    done_roots, failed, failed_paths = [], 0, set()
    for r in roots:
        if not args.path and str(r.path) not in prev_all:
            prev = fingerprints(r)   # root not in the checkpoint yet (also the very first run) — take now as the baseline instead of treating every file as changed
        else:
            prev = prev_all.get(str(r.path), {}).get("files", {})
            prev = {k: (v.get("sha256", "").lower() if isinstance(v, dict) else v) for k, v in prev.items()}
        a = audit(r, live, prev)
        if not needs_work(a, args.full):
            done_roots.append(r)
            continue
        before = len(r.leaves()) + len(r.archived())
        run_root(r, a, args, model)
        problems = verify(r, before, live_keys(all_roots), args.dry_run)
        if problems:
            failed += 1
            failed_paths.add(str(r.path))
        done_roots.append(r)
        cost = sum(x["cost"] for x in r.llm)
        lines += [f"## {r.group}/{r.name}", f"- audit: delta {len(a['delta'])}, unrouted {len(a['unrouted'])}, recent {len(a['recent'])}, "
                  f"big {len(a['big'])}, nofm {len(a['nofm'])}, dead {len(a['dead'])}, struck {len(a['struck'])}, "
                  f"dead_lines {len(a['dead_lines'])}, loose {len(a['loose'])}, l0_over {a['l0_over']}",
                  *[f"- {x}" for x in r.actions], *[f"- **not done** {x}" for x in r.issues],
                  *[f"- **verify** {x}" for x in problems],
                  *([f"- LLM {len(r.llm)} calls: " + ", ".join(f"{x['model']} {x['sec']}s in {x['in']} out {x['out']} ${x['cost']:.3f}" for x in r.llm)] if r.llm else []), ""]
        print(f"{r.group}/{r.name}: {len(r.actions)} actions, {len(r.issues)} issues, {len(problems)} problems, ${cost:.3f}", flush=True)
    total = sum(x["cost"] for r in roots for x in r.llm)
    lines.insert(2, f"- roots processed {sum(1 for l in lines if l.startswith('## '))} / total {len(roots)}, LLM cost total ${total:.3f}, roots failing verification {failed}")
    (out_dir / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report: {out_dir / 'REPORT.md'}")
    if not args.dry_run and not args.only and not args.path:
        # per root: a root that failed verification keeps its previous entry, so its changes are retried next run
        snap = {"schema_version": 3, "updated_at": NOW.isoformat(), "roots": {}}
        for r in all_roots:
            k = str(r.path)
            if k in failed_paths:
                if k in prev_all:
                    snap["roots"][k] = prev_all[k]
                continue
            snap["roots"][k] = {"name": r.name, "files": fingerprints(r)}
        tmp = CFG.checkpoint.with_suffix(".new")
        tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(CFG.checkpoint)
    sys.exit(3 if failed else 0)


if __name__ == "__main__":
    main()

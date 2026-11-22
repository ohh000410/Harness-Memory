"""Memory consolidation (on request only) — gathers lasting knowledge (rules, procedures, contacts, vendors, ...) spread
over several leaves into one new leaf.

usage:
  python -X utf8 consolidate.py propose (--only ROOT | --path DIR) [--index INDEX] [--model MODEL] [--config FILE]
      Changes no files. Writes <root>/raw/consolidate/plan-<time>.json and plan-<time>.md for a person to review.
  python -X utf8 consolidate.py apply <plan.json>
      After the user has checked the preview. Applies only the accepted groups.

The LLM returns only the new leaf body (rewritten) and, per source leaf, the line numbers to move. Code checks:
  - line numbers are outside frontmatter and not blank
  - fact check: every number, date, address and model name of the moved lines is in the new body / the new body has
    nothing that is not in the source leaves
  - where sources disagree the LLM lists conflicts and does not pick one
apply: stop if a source leaf changed after the proposal → originals to raw/legacy/consolidate-<time>/ → new leaf (ending
  with one "Sources:" line, not links) → the lines removed from the source leaves, plus one
  "Shared content: [[new leaf]] (related)" line → one line in MEMORY.md (## Leaves for a small root, else Recent — the
  next nightly curate routes it to an index) → one log.md line.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from curate import CTX_CHARS, HOOK, LIMIT, STAMP, TODAY, TYPES, Doc, Root, call_llm, configure, frontmatter, is_small, kebab  # noqa: E402

MAX_GROUPS = 3
# Fact tokens: URLs, mail addresses, dates, numbers, model names mixing digits (ABC-1234), upper-case abbreviations.
# Hyphenated plain words (cut-to-size) are not facts. Proper nouns without digits are not caught → the user checks
FACT = re.compile(r"https?://[^\s)>\]]+|[\w.+-]+@[\w-]+\.[\w.]+|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}"
                  r"|\d[\d,]*(?:\.\d+)?|[A-Za-z0-9]*(?:\d[A-Za-z0-9]*[-_]|[-_][A-Za-z0-9]*\d)[A-Za-z0-9_-]*|[A-Z]{2,}[A-Za-z0-9]*")
ENUM = re.compile(r"(?m)^\s*(?:[-*]\s+)?\d+[.)]\s")   # list numbering such as "1. " is not a fact


WIKI = re.compile(r"\[\[[^\]]*\]\]")


# upper-case emphasis words are not abbreviations
CAPS_WORDS = {"NOT", "NO", "DO", "DONT", "MUST", "NEVER", "ALWAYS", "ONLY", "ALL", "AND", "OR", "IF", "THE", "IS", "ARE", "NOTE",
              "IMPORTANT", "WARNING", "TODO", "OK", "YES", "STOP", "DONE", "FAIL", "PASS", "NEW", "OLD"}


def content_lines(text):
    """Content lines without frontmatter, headings and link lines"""
    _, end = frontmatter(text)
    return [l for l in text[end:].split("\n") if l.strip() and not re.fullmatch(r"\s*#+.*|\s*(Shared content: )?\[\[.*\]\].*", l)]


# Negation is a fact too: "Do not disable backups" rewritten as "Disable backups" keeps every number and name.
# Only its presence is compared (one token), so "don't" vs "do not" is not a difference
NEG = re.compile(r"(?i)\b(?:not|no|never|none|nothing|cannot|without|\w+n't|forbid\w*|prohibit\w*)\b|금지|않|못|없|말 것|마라|말라")


def facts(text):
    # [[link]] names are not facts — leaf names containing digits would count as facts
    text = ENUM.sub(" ", WIKI.sub(" ", text))
    return ({f.replace(",", "") for f in FACT.findall(text)} - CAPS_WORDS) | ({"<negation>"} if NEG.search(text) else set())


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def numbered(text):
    return "\n".join(f"{i:04d}| {l}" for i, l in enumerate(text.split("\n"), 1))


def scope(root, index):
    """Leaves to consolidate — all, or only those linked from one index (a big root exceeds CTX_CHARS in one go)"""
    if not index:
        return root.leaves()
    p = root.path / "indexes" / (index if index.endswith(".md") else index + ".md")
    if not p.exists():
        sys.exit(f"ERROR no such index {p.name}")
    text = Doc(p).text
    return [f for f in root.leaves() if f"distilled/{f.name})" in text]


def prompt(root, leaves):
    parts = [f"# Memory folder: {root.name}, {len(leaves)} notes", ""]
    for f in leaves:
        parts += [f"## {f.name}", numbered(Doc(f).text), ""]
    parts.append(f"""## Task
Find lasting knowledge that is spread over several notes or buried inside work-item notes — rules, procedures, contacts,
vendors, organization or program facts — that a future session would want in one place. Ongoing status of one item
(a single purchase's progress, one quote's amount) stays where it is.
Return JSON: {{"groups": [ ... at most {MAX_GROUPS} ... ]}}, each:
{{"file": "<reference|feedback>_<kebab-topic>.md", "type": "reference" or "feedback", "title": "<short title>",
  "description": "<one line, what the note holds>",
  "body": "<markdown in the same language as the taken lines (do not translate), written ONLY from them; keep every number, date, name and model exactly>",
  "take": [{{"leaf": "<file>.md", "lines": [<line numbers to move out of that note>]}}],
  "conflicts": ["<one line where the taken lines disagree with each other — do not pick one>"]}}
Take whole lines only, never frontmatter (between the --- lines) or link lines. If nothing qualifies, return {{"groups": []}}.
Optional per group: "upward": "<one line>" when the knowledge is the person's general working preference or method that
belongs above this folder (it is still gathered here; the user decides whether to move it up).""")
    return "\n".join(parts)


def check(root, g, texts):
    """(acceptable, reasons, lines to move)"""
    why, moved = [], []
    file = str(g.get("file", ""))
    if not (re.fullmatch(r"(reference|feedback)_(?:[^\W_]|-)+\.md", file) and file == file.lower()):
        why.append(f"file name not in the expected form: {g.get('file')}")
    elif (root.path / "distilled" / g["file"]).exists() or (root.path / "archive" / g["file"]).exists():
        why.append(f"file already exists: {g['file']}")
    if g.get("type") not in TYPES:
        why.append(f"not a type: {g.get('type')}")
    for t in g.get("take", []):
        leaf = t.get("leaf")
        if leaf not in texts:
            why.append(f"no such leaf: {leaf}")
            continue
        lines = texts[leaf].split("\n")
        _, end = frontmatter(texts[leaf])
        fm_lines = texts[leaf][:end].count("\n")
        for n in t.get("lines", []):
            if not isinstance(n, int) or n <= fm_lines or n > len(lines):
                why.append(f"{leaf} line {n}: inside frontmatter or out of range")
            elif lines[n - 1].strip():   # blank lines between paragraphs just go along — not a reason to reject
                moved.append(lines[n - 1])
    body = str(g.get("body", ""))
    if not moved or not body.strip():
        why.append("no lines to move or no body")
    missing = facts("\n".join(moved)) - facts(body)
    added = facts(body) - facts("\n".join(texts[t["leaf"]] for t in g.get("take", []) if t.get("leaf") in texts))
    if missing:
        why.append(f"facts missing from the new body: {sorted(missing)}")
    if added:
        why.append(f"facts not in the sources: {sorted(added)}")
    return not why, why, moved


def new_leaf_text(g):
    slug = kebab(Path(g["file"]).stem)
    sources = ", ".join(t["leaf"] for t in g["take"])
    desc = g["description"].replace('"', "'")
    return (f"---\nname: {slug}\ndescription: \"{desc}\"\ntype: {g['type']}\ncreated: {TODAY}\n---\n\n"
            f"{g['body'].strip()}\n\nSources: {sources}\n")


def propose(root, model, index):
    leaves = scope(root, index)
    texts = {f.name: Doc(f).text for f in leaves}
    hashes = {f.name: sha(f) for f in leaves}   # taken together with the text the LLM sees — apply checks against this state
    size = sum(len(t) for t in texts.values())
    if size > CTX_CHARS:
        sizes = [(p.stem, sum(len(texts.get(f.name, "")) for f in scope(root, p.name))) for p in root.index_files()]
        sys.exit(f"TOO_BIG {size:,} chars > {CTX_CHARS:,} — use --index, one index at a time: "
                 + ", ".join(f"{n} {s:,} chars" for n, s in sizes))
    dec = call_llm(root, model, prompt(root, leaves), "high")
    groups, names = [], set()
    for g in dec.get("groups", [])[:MAX_GROUPS]:
        ok, why, moved = check(root, g, texts)
        if ok and len(new_leaf_text(g).encode("utf-8")) > LIMIT:
            ok, why = False, [f"new leaf over {LIMIT} bytes"]
        if ok and g["file"] in names:   # two groups may not write the same new file
            ok, why = False, [f"same file name as an earlier group: {g['file']}"]
        if ok:
            names.add(g["file"])
        groups.append(dict(g, ok=ok, why=why, moved=moved))
    out = root.path / "raw" / "consolidate"
    out.mkdir(parents=True, exist_ok=True)
    plan = {"root": str(root.path), "stamp": STAMP, "model": model, "cost": sum(x["cost"] for x in root.llm),
            "sha": {t["leaf"]: hashes[t["leaf"]] for g in groups if g["ok"] for t in g.get("take", []) if t.get("leaf") in texts},
            "groups": groups}
    pj = out / f"plan-{STAMP}.json"
    pj.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    md = [f"# Consolidation proposal {STAMP} — {root.name}", "", f"Cost ${plan['cost']:.3f}. Apply: `consolidate.py apply {pj}` (accepted groups only)", ""]
    for i, g in enumerate(groups, 1):
        md += [f"## {i}. {g.get('title')} → distilled/{g.get('file')}  [{'accepted' if g['ok'] else 'rejected'}]"]
        md += [f"- Rejected because: {w}" for w in g["why"]]
        md += [f"- Sources disagree: {c}" for c in g.get("conflicts", [])]
        md += [f"- Candidate for a higher-level memory: {g['upward']}"] if g.get("upward") else []
        md += ["", "### New note", "```", new_leaf_text(g) if g["ok"] else str(g.get("body", "")), "```", "", "### Lines removed from sources"]
        for t in g.get("take", []):
            md += [f"- {t.get('leaf')}"] + [f"  - `{texts.get(t.get('leaf'), '').split(chr(10))[n - 1].strip()}`"
                                            for n in t.get("lines", []) if isinstance(n, int) and 0 < n <= len(texts.get(t.get("leaf"), "").split("\n"))]
        md.append("")
    pm = out / f"plan-{STAMP}.md"
    pm.write_text("\n".join(md), encoding="utf-8")
    print(f"PLAN {pm}")
    print(f"groups {len(groups)}, accepted {sum(g['ok'] for g in groups)}, ${plan['cost']:.3f}")


def apply(plan_path):
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    root = Root("main", Path(plan["root"]))
    for leaf, h in plan["sha"].items():
        if sha(root.path / "distilled" / leaf) != h:
            sys.exit(f"STALE {leaf} changed after the proposal — run propose again")
    groups = [g for g in plan["groups"] if g["ok"]]
    if not groups:
        sys.exit("NOTHING no accepted groups")
    for g in groups:   # the new file's place must still be free — nothing is ever overwritten
        if (root.path / "distilled" / g["file"]).exists() or (root.path / "archive" / g["file"]).exists():
            sys.exit(f"STALE {g['file']} exists now — run propose again")
    legacy = root.path / "raw" / "legacy" / f"consolidate-{STAMP}"
    touched = {t["leaf"] for g in groups for t in g["take"]}
    for rel in [f"distilled/{l}" for l in sorted(touched)] + ["MEMORY.md", "log.md"]:
        if (root.path / rel).exists():
            (legacy / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root.path / rel, legacy / rel)
    small = is_small(root)
    mem = root.doc("MEMORY.md")
    log = root.doc("log.md") if (root.path / "log.md").exists() else None
    drop = {}
    for g in groups:
        (root.path / "distilled" / g["file"]).write_bytes(new_leaf_text(g).encode("utf-8"))
        slug = kebab(Path(g["file"]).stem)
        for t in g["take"]:
            drop.setdefault(t["leaf"], {"lines": set(), "links": []})
            drop[t["leaf"]]["lines"] |= set(t["lines"])
            drop[t["leaf"]]["links"].append(slug)
        hook = g["description"] if len(g["description"]) <= HOOK else g["description"][:HOOK - 1] + "…"
        line = f"- [{g['title']}](distilled/{g['file']}) — {hook}"
        sec = "## Leaves" if small and "## Leaves" in mem.text else "## Recent / pending curate"
        if sec not in mem.text:
            mem.text = mem.text.rstrip("\n") + f"\n\n{sec}\n"
        mem.text = re.sub(r"(?m)^%s\n" % re.escape(sec), lambda m: f"{sec}\n{line}\n", mem.text, count=1)
        if log is not None:
            log.text = log.text.rstrip("\n") + f"\n- [{TODAY}] consolidated | {slug} ← {', '.join(t['leaf'] for t in g['take'])}\n"
    retired = {}   # source whose content all moved → slug of the new leaf (merged)
    for leaf, d in drop.items():
        doc = root.doc(f"distilled/{leaf}")
        kept = [l for i, l in enumerate(doc.text.split("\n"), 1) if i not in d["lines"]]
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).rstrip("\n")
        if not content_lines(text + "\n"):   # after rstrip the closing --- has no newline, so frontmatter would not be recognized
            # do not keep an empty shell of headings and links — it only adds to the note count. The original is in legacy above
            retired[leaf] = d["links"][0]
            doc.text = doc.orig
            continue
        doc.text = text + "\n\n" + "\n".join(f"Shared content: [[{s}]] (related)" for s in d["links"]) + "\n"
    if retired:
        names = {}
        for leaf, slug in retired.items():
            fm, _ = frontmatter(root.doc(f"distilled/{leaf}").text)
            for k in {Path(leaf).stem, fm.get("name", "")} - {""}:
                names[k] = slug
        # drop their lines from indexes and MEMORY.md, and turn [[old name]] in other leaves into [[new leaf]]
        for p in [root.path / "MEMORY.md", *root.index_files()]:
            if p.exists():
                d = root.doc(root.rel(p))
                d.text = "\n".join(l for l in d.text.split("\n")
                                   if not (l.lstrip().startswith("- ") and any(f"distilled/{x})" in l for x in retired)))
        for f in root.leaves():
            if f.name in retired:
                continue
            d = root.doc(f"distilled/{f.name}")
            d.text = re.sub(r"\[\[([^\]|#]+)\]\]", lambda m: f"[[{names.get(m.group(1).strip(), m.group(1))}]]", d.text)
        if log is not None:
            log.text = log.text.rstrip("\n") + "".join(f"\n- [{TODAY}] merged | {l} → {s} (all content moved, original in legacy)" for l, s in retired.items()) + "\n"
    for rel, d in root.writes.items():
        if d.changed() and Path(rel).name not in retired:
            if not (legacy / rel).exists():   # every file changed here gets a pre-change copy (indexes and other notes too)
                (legacy / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(d.path, legacy / rel)
            d.path.write_bytes(d.data())
    for leaf in retired:
        (root.path / "distilled" / leaf).unlink()   # the original is in legacy/consolidate-<time>/distilled
    print(f"APPLIED {len(groups)} groups, {len(drop)} source leaves ({len(retired)} merged into legacy), originals in {legacy}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("--only")
    p.add_argument("--path")
    p.add_argument("--model", help="default: model_heavy from the config")
    p.add_argument("--index", help="only leaves linked from this one index (big roots)")
    p.add_argument("--config", help="JSON config file (default: env MEMORY_CURATION_CONFIG, else config.json in the repository root)")
    sub.add_parser("apply").add_argument("plan")
    a = ap.parse_args()
    if a.cmd == "apply":
        return apply(a.plan)
    cfg = configure(a.config)
    if a.path:
        root = Root("main", Path(a.path))
    else:
        root = next((Root(g, base / a.only) for g, base in cfg.groups.items() if (base / str(a.only)).is_dir()), None)
        if root is None:
            sys.exit(f"ERROR no such root {a.only}")
    propose(root, a.model or cfg.model_heavy, a.index)


if __name__ == "__main__":
    main()

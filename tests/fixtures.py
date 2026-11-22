"""Synthetic memory roots for the tests — invented content only. usage: python fixtures.py OUTDIR"""
import os
import sys
import time
from pathlib import Path

MARK = "superseded:"
OLD = time.time() - 90 * 86400


def w(p, text, old=False, bom=False, crlf=False):
    p.parent.mkdir(parents=True, exist_ok=True)
    if crlf:
        text = text.replace("\n", "\r\n")
    p.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    if old:
        os.utime(p, (OLD, OLD))


def note(name, desc, body, typ="reference"):
    return f"---\nname: {name}\ndescription: \"{desc}\"\ntype: {typ}\n---\n\n{body}\n"


def build(out):
    out = Path(out)
    # ---------- alpha: indexed root ----------
    a = out / "alpha"
    tools = [f"tool_{i:02d}" for i in range(1, 9)]
    projs = [f"project_task-{i}" for i in range(1, 5)]
    for t in tools:
        w(a / f"distilled/{t}.md", note(t.replace("_", "-"), f"Tool {t} usage", f"# {t}\n\nUse {t} with flag --x={t[-2:]}."))
    for p in projs:
        w(a / f"distilled/{p}.md", note(p.replace("_", "-"), f"Work item {p}", f"# {p}\n\nStatus: in progress since 2025-03-01.", "project"))
    # stale project note (archived) and stale project note linked from Hot (kept)
    w(a / "distilled/project_old-purchase.md", note("project-old-purchase", "Old purchase", "Ordered 3 cables, PO 4411.", "project"), old=True)
    w(a / "distilled/project_hot-item.md", note("project-hot-item", "Hot item", "Waiting on vendor reply.", "project"), old=True)
    # old feedback note: not archived
    w(a / "distilled/feedback_style.md", note("feedback-style", "Writing style", "Prefer short sentences.", "feedback"), old=True)
    # no frontmatter, caps in name
    w(a / "distilled/Note_With_Caps.md", "# Caps note heading. Second sentence here\n\nBody text with [[tool-01]] link.\n")
    # frontmatter without name
    w(a / "distilled/reference_noname.md", "---\ndescription: \"Has a description\"\ntype: reference\n---\n\nSome body.\n")
    # struck text, one with arrow, one with an existing marker
    w(a / "distilled/reference_ports.md", note("reference-ports", "Port numbers",
      "# Ports\n\n- Web port: ~~8080~~ → 9090\n- DB: ~~5432 was used~~ now 6543\n"
      f"- Cache: ~~11211~~ 11311 (2025-05-01 {MARK} vendor changed it)\n- Unchanged line\n"))
    # link to note only in raw/
    w(a / "raw/old-procedure.md", "# Old procedure\n\nlegacy text\n")
    w(a / "distilled/reference_procedure.md", note("reference-procedure", "Procedure", "See [[old-procedure]] and [[tool-02]] and [[nowhere-note]]."))
    # big note splittable at headings
    sections = "\n\n".join(f"## Section {i}\n\n" + ("Line of filler text for section %d. " % i) * 40 for i in range(1, 7))
    w(a / "distilled/reference_big.md", note("reference-big", "Big reference", "# Big\n\n" + sections))
    # unrouted note
    w(a / "distilled/reference_unrouted.md", note("reference-unrouted", "Not in any index", "Orphan content."))
    # loose note at root, linked from MEMORY.md by bare name
    w(a / "scratch-idea.md", note("scratch-idea", "Loose idea", "An idea saved at the root."))
    idx_tools = "# Index: tools\n\n" + "\n".join(f"- [{t}](../distilled/{t}.md) — tool {t}" for t in tools) + \
        "\n- [Missing](../distilled/reference_missing.md) — gone\n- [Caps](../distilled/Note_With_Caps.md) — caps\n" \
        "- [No name](../distilled/reference_noname.md) — no name\n- [Ports](../distilled/reference_ports.md) — ports\n" \
        "- [Procedure](../distilled/reference_procedure.md) — proc\n- [Big](../distilled/reference_big.md) — big\n" \
        "- [Style](../distilled/feedback_style.md) — style\n"
    w(a / "indexes/tools.md", idx_tools)
    w(a / "indexes/projects.md", "# Index: projects\n\n" + "\n".join(f"- [{p}](../distilled/{p}.md) — {p}" for p in projs) +
      "\n- [Old purchase](../distilled/project_old-purchase.md) — old\n- [Hot item](../distilled/project_hot-item.md) — hot\n")
    w(a / "MEMORY.md", "# Memory Index: alpha\n\nLast curated: 2025-01-01 by curate-memory.\n\n## Hot\n"
      "- [Hot item](distilled/project_hot-item.md) — waiting\n\n## Topic indexes\n- [tools](indexes/tools.md) — tools\n"
      "- [projects](indexes/projects.md) — projects\n- [gone](indexes/gone.md) — missing index\n\n## Recent / pending curate\n"
      "- [Idea](scratch-idea.md) — loose idea\n- [Task 1](distilled/project_task-1.md) — task\n")
    w(a / "log.md", "- [2025-01-01] curated | routing — layout (1 actions)\n")

    # ---------- beta: small root with ## Leaves ----------
    b = out / "beta"
    for i in range(1, 8):
        w(b / f"distilled/reference_fact-{i}.md", note(f"reference-fact-{i}", f"Fact {i}", f"Fact number {i}: value {i * 7}."))
    w(b / "distilled/reference_crlf.md", "---\nname: reference-crlf\ntype: reference\n---\n\nCRLF body. Another sentence.\n", bom=True, crlf=True)
    w(b / "distilled/project_stale.md", note("project-stale", "Stale", "Old work.", "project"), old=True)
    w(b / "distilled/reference_links.md", note("reference-links", "Links", "Refers to [[raw-only]] and [[project-stale]] and [[reference-fact-1]]."))
    w(b / "raw/2025/raw_only.md", "raw only\n")
    w(b / "distilled/reference_struck.md", note("reference-struck", "Struck", "- Rule: ~~never do X~~ do Y\n- ~~whole line struck~~\n"))
    # ~~ inside code is not struck text: a ~~~ fence, a ``` fence and inline code
    w(b / "distilled/reference_code.md", note("reference-code", "Code samples",
      "~~~bash\necho ~~not struck~~\n~~~\n\n```\nx = '~~kept~~'\n```\n\nInline `~~kept~~` too.\n"))
    leaves = "\n".join(f"- [Fact {i}](distilled/reference_fact-{i}.md) — fact {i}" for i in range(1, 8))
    w(b / "MEMORY.md", "# Memory Index: beta\n\n## Hot\n- None.\n\n## Leaves\n" + leaves +
      "\n- [CRLF](distilled/reference_crlf.md) — crlf\n- [Stale](distilled/project_stale.md) — stale\n"
      "- [Links](distilled/reference_links.md) — links\n- [Dead](distilled/reference_dead.md) — dead line\n"
      "- [Web](https://example.com) — external\n\n## Recent / pending curate\n")
    w(b / "log.md", "".join(f"- [2025-01-{(i % 28) + 1:02d}] curated | routing — entry {i} with some padding text to grow\n" for i in range(1200)))

    # ---------- gamma: big index, unsplittable big note ----------
    c = out / "gamma"
    for i in range(1, 28):
        w(c / f"distilled/reference_item-{i:02d}.md", note(f"reference-item-{i:02d}", f"Item {i}", f"Item {i} body."))
    w(c / "distilled/reference_wall.md", note("reference-wall", "Wall of text", "# Wall\n\n" + "word " * 2000))
    w(c / "indexes/everything.md", "# Index: everything\n\n" + "\n".join(
        f"- [Item {i}](../distilled/reference_item-{i:02d}.md) — item {i}" for i in range(1, 28)) +
      "\n- [Wall](../distilled/reference_wall.md) — wall\n")
    w(c / "MEMORY.md", "# Memory Index: gamma\n\n## Hot\n- [everything](indexes/everything.md) — all\n\n## Topic indexes\n"
      "- [everything](indexes/everything.md) — all\n\n## Recent / pending curate\n")


if __name__ == "__main__":
    build(sys.argv[1])
    print("generated", sys.argv[1])

## Memory

<!-- Paste into the session instructions (CLAUDE.md, AGENTS.md, or a SessionStart hook). Format details: docs/memory-layout.md -->

- **Where**: the session's `autoMemoryDirectory`. Facts tied to this folder go there; ways of working, tool environment and user preferences go to the parent folder's memory.
- **File**: `distilled/<type>_<kebab-slug>.md` (type = user | feedback | project | reference). Frontmatter `name` (the file name in kebab case) · `description` · `type` · `created: YYYY-MM-DD`.
- **Body**: user = the fact / feedback = rule + **Why:** + **How to apply:** / project = context + state with absolute dates / reference = pointer + purpose. Record limits and counter-evidence too.
- **Register**: one line in `MEMORY.md` under `## Recent / pending curate` (link + hook ≤ 80 chars, no facts) and one line in `log.md`: `- [YYYY-MM-DD] saved | <slug> — <one line>`. Indexes, splits and link repair are done by the nightly `curate-memory` run — do not reorganize by hand.
- **Links**: `[[name]] (refines | contradicts | depends-on | example-of | related)`. One, the most specific.
- **Contradictions**: never rely on one side of a `contradicts` pair. Fix the confirmed side; move the wrong note to `raw/legacy/` and delete it.
- **Find**: `MEMORY.md` → the relevant index → the note. Old project notes are in `archive/` (not indexed) — search there when the index has nothing.
- **What to save**: conditions, results, decisions, open items and next steps the next session needs (project); methods and pitfalls reusable elsewhere (feedback, parent folder); the reason when your recommendation differed from the user's choice (feedback). Do not save what the code or git history already records.

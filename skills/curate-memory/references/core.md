# Core contract

## Layers

- `MEMORY.md` (L0): sparse router. Sections in order: pointer line to the parent folder's memory (sub-folder roots), `## Hot` (current/unresolved, ≤5 links), `## Topic indexes` (one line per `indexes/*.md`), `## Leaves` (small roots only), `## Recent / pending curate` (new notes not yet routed; always last). Link lines above the first heading (Claude Code's built-in format) are pending as well. Each line = link + hook ≤80 chars. Never link to `raw/`. Keep under ~800 estimated tokens (ASCII ≈ 4 characters per token, other scripts ≈ 1 per character) and 25 links.
- `indexes/<topic>.md` (L1): one topic per file, one line per note (`- [title](../distilled/<file>.md) — hook`). A note belongs to exactly one primary index; add a second link only for a genuinely different retrieval intent. Create a new index only when ≥3 notes share a topic that no existing index covers; otherwise use the closest existing index.
- `distilled/*.md` (L2): the only place facts live. Frontmatter `name` (kebab-slug = filename) · `description` · `type` · `created`.
- `raw/` (L3): provenance and pre-change copies. Never a retrieval source.
- `log.md`: append-only chronology `- [YYYY-MM-DD] <action> | <slug> — <one line>`. Rotate when larger than 64 KB: keep the newest 30 lines, move the rest to `raw/legacy/log-rotated-<timestamp>.md`.

## Small roots

A root with fewer than 15 notes and no `indexes/` routes notes directly from L0 under a `## Leaves` section. When it reaches 15, the nightly curate groups every note (including those under `## Leaves`) into 3–7 topic indexes once; a root that has `indexes/` keeps using them even below 15. An index over 25 notes (or 150k characters of notes) is split by topic into sibling indexes. Project notes unchanged for 60 days (agent roots: 180) move to `archive/` (out of the indexes, still on disk).

## Frontmatter repair

If `name` is missing or empty, set it from the filename (kebab-case of the whole stem, e.g. `reference_foo.md` → `reference-foo`). A missing `description` is taken from the title or first sentence. Leave every other field as found.

## Encoding

New files: UTF-8 without BOM, LF line endings. Existing files keep their BOM and line endings. Text in any language is kept as-is.

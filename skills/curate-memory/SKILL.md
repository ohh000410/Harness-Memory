---
name: curate-memory
description: Curate file-based memory roots (session roots and agent roots, layout in docs/memory-layout.md). Runs nightly from the scheduler and on "curate memory" / /curate-memory for the current folder's root. On request only, "consolidate memory" gathers lasting knowledge spread over several notes into one new note (consolidate.py, the user confirms the preview before anything is written). Code applies every edit; the LLM only returns JSON decisions (routing, contradictions, stale hooks, split points) from one root's text.
---

# Curate memory

Run the script; do not curate by hand. Root name = last path segment of `autoMemoryDirectory` in the folder's `.claude/settings.local.json`. Then summarize the report it prints.

```
python -X utf8 <skill>/scripts/curate.py --only <root>
```

- `--dry-run` reports the work without changes or LLM calls (`--llm-dry` also asks the LLM, still no writes). `--no-llm` does only the code fixes.
- `--path <dir>` runs one folder anywhere (tests). `--only` and `--path` leave the checkpoint alone.
- `--config <file>` (or env `MEMORY_CURATION_CONFIG`, or `config.json` in the repository root): memory locations, log folder, `claude` command, `model` (routing, index splits), `model_heavy` (note splits, consolidation), archive days — see `config.example.json`.
- Report: `<logs>/curate-report-<time>/REPORT.md` (a `--path` run: next to that folder) — per root: findings, actions, what could not be done, verification, LLM calls and cost. Exit 3 = a root failed verification; that root's checkpoint entry is not advanced, so its changes are retried next night (other roots advance).
- First run: roots not yet in the checkpoint take their current state as the baseline — only findings are processed, not every note as "changed".

## What runs where

- **Code**: roots changed since the last run (checkpoint) or with findings. Loose notes → `distilled/`; index lines to missing notes dropped; missing frontmatter `name` (from the file name) and `description` (title or first sentence); `[[links]]` whose target exists only in `raw/` unwrapped; `~~struck~~` text (outside code) replaced by a dated `superseded:` note that points at the previous version; `log.md` over 64 KB rotated; backups of changed files in `raw/legacy/<action>-<time>/`; `Last curated` and a `log.md` line; re-audit after writing.
- **LLM** (`claude -p` with no tools and no settings, its own system prompt — only that root's text): routing of new notes (an existing index, a new index only with ≥ 3 notes, or `## Leaves` in a small root; a root reaching 15 notes without indexes is grouped once into 3–7 topics), contradiction pairs among changed notes and their index neighbours, stale hooks, shorter hooks when `MEMORY.md` is over its limit, split points of a big note that has no `##` headings to cut at.
- **Archive**: `type: project` notes unchanged for 60 days (agent roots 180) and not under `## Hot` move to `archive/`; their index lines are dropped. Nothing is deleted.
- **Index split**: an index with more than 25 notes, or more than 150k characters of notes, is split by topic into sibling indexes (largest first, one per night). The LLM sees only index lines and descriptions; code moves the lines verbatim; the old index goes to `raw/legacy/`.
- **Note split**: notes over 8 KB are cut verbatim (never inside a table or code block); the parts must join back to the original body byte for byte. The original keeps its name and `created`; parts are `<stem>-<n>.md` with `[[original]] (refines)`.

Format: [references/core.md](references/core.md). A root with a `.curate-ignore` file is skipped.

## Consolidate (on request only)

"Consolidate memory" moves lasting knowledge (rules, procedures, contacts, vendors) that is spread over several notes or buried in project notes into one new note. Never part of the nightly run.

```
python -X utf8 <skill>/scripts/consolidate.py propose --only <root> [--index <index>]   # writes only raw/consolidate/plan-<time>.md/.json
python -X utf8 <skill>/scripts/consolidate.py apply <plan.json>                        # after the user has read the preview
```

- **propose**: the LLM drafts up to 3 bundles — a new body and, per source note, the line numbers that move. Code rejects a bundle when moved lines are frontmatter or out of range (blank lines may move with their paragraph), when a number, date, address or model name from a moved line is missing from the new body, or when the new body states a fact that no source contains. The fact check compares tokens only — numbers, dates, addresses, model names, upper-case abbreviations and whether a negation is present; it does not understand sentences (a changed condition or a swapped subject passes). Disagreements between sources are listed, not resolved. Show the preview to the user as is: the user is the check for meaning and for proper nouns in non-Latin scripts.
- A root whose notes exceed 150k characters prints `TOO_BIG` with per-index sizes and stops — run `--index` one index at a time.
- "Candidate for a higher-level memory" in the preview = knowledge that fits a folder above this one. Moving it is the user's decision. Each propose can bundle differently — propose again if the result is not useful.
- **apply**: accepted bundles only; stops with `STALE` if a source changed after propose. Originals → `raw/legacy/consolidate-<time>/`; the new note ends with a `Sources:` line; sources lose the moved lines and get `Shared content: [[new note]] (related)`; one line in `MEMORY.md` (`## Leaves` in a small root, else Recent); a `consolidated` line in `log.md`. A source left with nothing but its title and links is retired: kept only in legacy, its index line removed, other notes' links to it pointed at the new note, a `merged` line in `log.md`.

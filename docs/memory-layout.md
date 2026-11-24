# Memory layout

A memory **root** is one folder. Sessions own one root per working folder (main roots); specialist agents own one root each (agent roots). Every root has the same shape:

```
<root>/
  MEMORY.md          L0  index loaded at session start
  indexes/<topic>.md L1  topic indexes (one line per note)
  distilled/*.md     L2  notes — the only place facts live
  archive/*.md           project notes untouched for a long time (out of the indexes)
  raw/               L3  provenance: work logs, pre-change copies (raw/legacy/), never a retrieval source
  log.md                 append-only chronology
```

## MEMORY.md (L0)

A sparse router, not a store. Sections in this order:

| Section | Content |
|---|---|
| title + `Last curated: <date>` | |
| pointer line | for a sub-folder root: one line pointing at the nearest ancestor folder's `MEMORY.md` (shared ways of working, tools, preferences) — present when that ancestor already had a memory root when this one was created |
| `## Hot` | current or unresolved items, at most 5 links |
| `## Topic indexes` | one line per `indexes/*.md` |
| `## Leaves` | only in small roots (under 15 notes, no `indexes/`): note links directly, instead of indexes |
| `## Recent / pending curate` | notes saved since the last curation; the nightly run moves them into indexes. Always the last section, so a line appended at the end of the file is pending |

Lines above the first `##` heading are pending too. That is where Claude Code's built-in memory format puts its one link per note, so a root written in that format (by agents that know only it) is converted on its first curation: note files move to `distilled/`, each link line moves to `## Leaves` or an index.

Each line is `- [title](path) — hook` with a hook of at most 80 characters. Facts, numbers and rules stay in the notes. Never link to `raw/`. Keep the file under about 800 estimated tokens and 25 links.

## Topic indexes (L1)

One topic per file, one line per note: `- [title](../distilled/<file>.md) — hook`. A note belongs to one primary index; add a second link only for a genuinely different retrieval intent. A new index needs at least 3 notes that no existing index covers.

- A root without indexes that reaches 15 notes is grouped once into 3–7 topic indexes (lasting knowledge apart from ongoing items).
- An index with more than 25 notes, or whose notes exceed 150k characters, is split by topic into sibling indexes.

## Notes (L2)

`distilled/<type>_<kebab-slug>.md`, one fact or decision per note.

```markdown
---
name: <file name without .md, in kebab case: feedback_short-replies.md → feedback-short-replies>
description: <one line, used to decide relevance>
type: user | feedback | project | reference
created: YYYY-MM-DD
---

<body>

Related: [[other-note]] (refines)
```

| type | body |
|---|---|
| user | a fact about the user (role, background, preference) |
| feedback | the rule + **Why:** + **How to apply:** |
| project | context and state of ongoing work, absolute dates |
| reference | a pointer to an external resource and what it is for |

Write limits and counter-evidence next to the claim ("X wins on A; untested on B"), not only the positive result.

**Links** — `[[name]] (relation)`: `refines` (updates or replaces), `contradicts`, `depends-on`, `example-of`, `related` (default). Pick the most specific one. A link to a name that does not exist yet is allowed.

**Contradictions** — notes linked by `contradicts` are not used one-sidedly. When the right side is confirmed, fix it; copy the wrong note to `raw/legacy/` and delete it.

## Archive

`type: project` notes that have not changed for 60 days (agent roots: 180 days, configurable) and are not under `## Hot` move to `archive/`; their index lines are dropped. Nothing is deleted — search `archive/` with any text search when an old item is needed. Rules, references and user facts never expire by age; they are replaced when proven wrong.

## log.md

`- [YYYY-MM-DD] <action> | <slug> — <one line>` (`saved`, `updated`, `moved`, `curated`, …). Rotated above 64 KB: the newest 30 lines stay, the rest go to `raw/legacy/`.

## Ownership — one fact, one place

| Content | Where |
|---|---|
| goals, conditions, results, decisions of a project | that folder's main root |
| ways of working, tool environment, user preferences shared by sub-folders | the nearest common parent folder's root |
| tool procedures, API facts, failure causes, workarounds | the specialist agent's own root (written only by that agent) |

A session reads its own root and its ancestors' roots, not siblings or children.

## Root naming

Main roots live in one central folder (default `~/.claude/projects/memories/`), named after the working folder relative to a workspace root, path separators replaced by `__`: `projects/webapp/frontend` → `workspace__projects__webapp__frontend`. `hooks/ensure-memory-dir.ps1` creates the root and points the folder's `.claude/settings.local.json` `autoMemoryDirectory` at it. Claude Code loads that root's `MEMORY.md` by itself; Codex gets it from `hooks/inject-memory-index.ps1` at session start, and Antigravity from `hooks/agy-session-start.ps1` on the first call of a conversation — so the three harnesses share one root per folder. The workspace folder itself gets no root — put shared memory in a first-level folder, or set `autoMemoryDirectory` by hand. Do not use `__` in folder names: `a\b` and `a__b` would map to the same root. If a folder's setting is lost while its root still exists, the hook reconnects the folder to that root without rewriting it. A folder whose settings already name an `autoMemoryDirectory` (for example a sub-folder you point at its parent's root on purpose) is left alone.

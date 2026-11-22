# memory-curation

A file-based memory format for Claude Code sessions and agents, and a nightly curator that keeps it small and navigable.

- **Format** — each working folder gets a memory root: a short `MEMORY.md` index loaded at session start, topic indexes, one note per fact, raw provenance. Sub-folders read their parents' memory, so shared rules live once. → [docs/memory-layout.md](docs/memory-layout.md)
- **Curator** (`curate.py`) — code does every edit; an LLM (`claude -p`, no tools) only returns JSON decisions from one root's text. Routes new notes into indexes, groups a growing root into topics, splits oversized indexes and notes, archives stale project notes, finds contradictions, repairs links and frontmatter, keeps backups of everything it changes.
- **Consolidation** (`consolidate.py`, on request) — gathers lasting knowledge scattered across notes into one note, with a fact check and a preview you approve before anything is written.
- **Folder hook** (`ensure-memory-dir.ps1`) — the first session in a folder creates its memory root and points Claude Code's `autoMemoryDirectory` at it.

No memory contents are included; the examples are invented.

## Requirements

- Windows (the hook and scheduler script are PowerShell; the Python scripts are portable)
- Python 3.10+, no extra packages
- [Claude Code](https://docs.claude.com/en/docs/claude-code) CLI on `PATH` (for the LLM decisions; `--no-llm` runs without it)

## Layout

```
config.example.json            copy to config.json and edit
docs/memory-layout.md          the memory format
templates/memory-rules.md      memory rules to paste into session instructions
hooks/ensure-memory-dir.ps1    SessionStart hook: per-folder memory roots
skills/curate-memory/          the skill: SKILL.md, references/, scripts/curate.py, scripts/consolidate.py
scripts/register-nightly.ps1   daily Task Scheduler entry
tests/                         synthetic fixtures and tests
```

## Install

1. Clone the repository and copy `config.example.json` to `config.json`. Set `workspace` (the folder whose sub-folders get memory roots) and, if you like, the other paths. If you set `roots`, list every group you want curated — the map replaces the default instead of merging with it (`archive_days` merges per group).
2. Make the skill visible to Claude Code — link `skills/curate-memory` to `~/.claude/skills/curate-memory` (`mklink /J` or a symlink); the scripts follow the link back to the repository and find `config.json`. If you copy the folder instead, set a user environment variable so the scripts (and the scheduled task) find the config: `setx MEMORY_CURATION_CONFIG C:\path\to\memory-curation\config.json`.
3. Register the hook and one setting in `~/.claude/settings.json`:

   ```json
   {
     "env": { "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": "1" },
     "hooks": {
       "SessionStart": [
         { "matcher": "*", "hooks": [ { "type": "command",
           "command": "powershell -NoProfile -ExecutionPolicy Bypass -File \"C:\\path\\to\\memory-curation\\hooks\\ensure-memory-dir.ps1\"" } ] }
       ]
     }
   }
   ```

   The env setting returns the shell to the session folder after each command. Without it, a session that `cd`s into a sub-folder and then compacts hands the hook that sub-folder, and a stray memory root appears there.
4. Paste [templates/memory-rules.md](templates/memory-rules.md) into your `CLAUDE.md` (or wherever your session instructions live).
5. Schedule the nightly run: `powershell -ExecutionPolicy Bypass -File scripts\register-nightly.ps1` (`-At 04:30`, `-Python <path>`, `-Remove`). The task runs as you, only while you are logged on, and a console window opens at run time; `python` and `claude` must be on the PATH the task sees (or pass `-Python` and set `"claude"` to a full path in the config).

Main roots are created by the hook. Agent roots (`roots.agent`, one folder per specialist agent) are optional: create a folder per agent in the same layout and let the agent write its own notes; the curator treats both groups the same except for `archive_days`. Remove the `agent` entry if you have none.

## Use

```
python -X utf8 skills/curate-memory/scripts/curate.py --dry-run          # what would change, no writes, no LLM
python -X utf8 skills/curate-memory/scripts/curate.py --only <root>      # one root now
python -X utf8 skills/curate-memory/scripts/consolidate.py propose --only <root>
```

In a session: "curate memory" or "consolidate memory" — see [SKILL.md](skills/curate-memory/SKILL.md). Each run writes a report (findings, actions, skipped items, verification, LLM cost) to the `logs` folder; a `--path` run writes it next to the target folder instead.

Tests: `python -X utf8 -m unittest discover tests` (synthetic roots in a temp folder, no LLM).

## Limits

- The LLM sees one root at a time; cross-root duplicates are not detected.
- Consolidation's fact check compares numbers, dates and Latin-script names; names in other scripts are left to the user's review of the preview.
- Archived notes are not indexed; find them with a text search over `archive/`.
- The hook rewrites the folder's `settings.local.json` with Windows PowerShell's JSON formatting (content kept, indentation changes).
- `consolidate.py apply` has no automated test yet; review the preview before applying.
- Tested on Windows 11 with Claude Code on one machine.

## License

MIT — see [LICENSE](LICENSE).

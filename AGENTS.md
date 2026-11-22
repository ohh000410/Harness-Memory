# Install guide for AI agents

You are installing harness-memory for the user on Windows. Ask before every step that changes their settings, and show what you will change.

1. Ask the user for the **workspace** folder (sub-folders of it get memory roots) and whether they use per-agent memory. Clone this repository to a stable place (not a temp folder).
2. Copy `config.example.json` to `config.json`. Set `workspace`. Set `roots` to exactly the groups they want (a `roots` map replaces the defaults; drop `agent` if unused).
3. Link the skill (from PowerShell, `cmd /c mklink /J`; create `%USERPROFILE%\.claude\skills` first if missing): `mklink /J "%USERPROFILE%\.claude\skills\curate-memory" "<repo>\skills\curate-memory"`. If a `curate-memory` skill already exists there, stop and ask.
4. Merge into `%USERPROFILE%\.claude\settings.json` (keep every existing key and hook): `env.CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR = "1"` and one `SessionStart` hook running `powershell -NoProfile -ExecutionPolicy Bypass -File "<repo>\hooks\ensure-memory-dir.ps1"`. Back up the file first.
5. Append `templates/memory-rules.md` to the user's `%USERPROFILE%\.claude\CLAUDE.md` (or where their session instructions live) — show the text first.
6. Verify without touching real memory: `python -X utf8 -m unittest discover tests` from the repo root, then `python -X utf8 skills/curate-memory/scripts/curate.py --dry-run` (memory untouched, no LLM; only a report is written) and summarize the report.
7. Only if the user agrees: `powershell -ExecutionPolicy Bypass -File scripts\register-nightly.ps1`.

Never run `curate.py` without `--dry-run` or `--path` during installation, and never point a test config at the user's real memory folders.

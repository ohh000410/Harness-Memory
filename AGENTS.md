# AGENTS.md

AI 에이전트용 설치·사용 안내. 사람용 소개는 `README.md`.
이 저장소는 Claude Code 세션·서브에이전트용 파일 메모리 형식(`docs/memory-layout.md`)과, 그 메모리를 매일 밤 정리하는 스킬 `curate-memory` 다.
작업 폴더를 연 세션마다 SessionStart 훅(Windows PowerShell)이 그 폴더의 메모리를 만들고 연결한다 — Windows 전용.

## 설치

설정을 바꾸는 단계마다 무엇을 바꿀지 보여 주고 사용자에게 먼저 묻는다.

1. 사용자에게 **workspace** 폴더(그 아래 폴더마다 메모리가 생김)와 서브에이전트 메모리를 쓰는지 확인한다(`memory: user` 인 서브에이전트 — 예: ohh000410/Subagents 플러그인 — 는 `~/.claude/agent-memory/` 에 씀). 이 저장소는 임시 폴더가 아닌 고정된 곳에 받는다.
2. `config.example.json` 을 `config.json` 으로 복사하고 `workspace` 를 적는다. `roots` 에는 정리할 그룹을 모두 적는다(`roots` 는 기본값과 합쳐지지 않음 — 서브에이전트 메모리를 안 쓰면 `agent` 를 지움).
3. 스킬 연결(PowerShell 에서는 `cmd /c mklink /J`, `%USERPROFILE%\.claude\skills` 가 없으면 먼저 만듦): `mklink /J "%USERPROFILE%\.claude\skills\curate-memory" "<저장소>\skills\curate-memory"`. 그 자리에 `curate-memory` 스킬이 이미 있으면 멈추고 묻는다.
4. `%USERPROFILE%\.claude\settings.json` 에 합친다(기존 키·훅은 모두 유지, 먼저 백업): `env.CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR = "1"`, 그리고 `powershell -NoProfile -ExecutionPolicy Bypass -File "<저장소>\hooks\ensure-memory-dir.ps1"` 를 실행하는 `SessionStart` 훅 하나.
5. `templates/memory-rules.md` 를 사용자의 `%USERPROFILE%\.claude\CLAUDE.md`(또는 세션 지침이 있는 곳) 끝에 붙인다 — 글을 먼저 보여 준다.
6. 실제 메모리를 건드리지 않고 확인: 저장소 폴더에서 `python -X utf8 -m unittest discover tests`, 그다음 `python -X utf8 skills/curate-memory/scripts/curate.py --dry-run`(메모리·LLM 안 건드림, 보고서만 씀) 후 보고서를 요약한다.
7. 사용자가 동의할 때만: `powershell -ExecutionPolicy Bypass -File scripts\register-nightly.ps1`.

설치 중에는 `curate.py` 를 `--dry-run` 이나 `--path` 없이 돌리지 않고, 시험용 설정이 사용자의 실제 메모리 폴더를 가리키게 하지 않는다.

## 쓰기

- "curate memory"(정리) → `curate.py --only <메모리 이름>`, 출력된 보고서를 요약한다. 메모리 이름 = 작업 폴더 `.claude/settings.local.json` 의 `autoMemoryDirectory` 마지막 폴더 이름
- "consolidate memory"(재구성) → `consolidate.py propose --only <메모리 이름>` 으로 미리보기만 만들고, 사용자가 확인한 뒤에만 `apply`
- 자세한 옵션·출력값은 `skills/curate-memory/SKILL.md`

## 지우기

1. 매일 정리 해제: `powershell -ExecutionPolicy Bypass -File scripts\register-nightly.ps1 -Remove`
2. `settings.json` 에서 4번의 `SessionStart` 훅과 `env` 항목을 뺀다(먼저 백업).
3. 스킬 연결 해제: `cmd /c rmdir "%USERPROFILE%\.claude\skills\curate-memory"` — 연결(정션)만 지움. `/s` 를 붙이면 저장소 안 파일까지 지워지니 붙이지 않는다.
4. 5번에서 붙인 저장 규칙을 지침 파일에서 뺀다.

메모리 폴더(`roots` 에 적은 곳)는 남는다. 작업 폴더마다 훅이 넣은 `.claude/settings.local.json` 의 `autoMemoryDirectory` 도 남으니, Claude Code 기본 메모리 위치로 돌아가려면 그 키를 지운다.

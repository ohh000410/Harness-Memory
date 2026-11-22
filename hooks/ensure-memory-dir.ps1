# Claude Code SessionStart hook: give every working folder under the workspace its own memory root.
# The first session in a folder creates <main root>/<workspace-name>__<relative path joined by __>/ with MEMORY.md and log.md,
# writes autoMemoryDirectory into the folder's .claude/settings.local.json (existing keys kept),
# and tells the session where to save. A folder that already has autoMemoryDirectory, or autoMemoryEnabled false, is left alone.
# Set CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR=1 in Claude Code's env: otherwise a `cd` into a sub-folder followed by
# /compact or /clear hands this hook the sub-folder as cwd and a stray root is created there.
$utf8 = [System.Text.UTF8Encoding]::new($false)
$inputText = [System.IO.StreamReader]::new([Console]::OpenStandardInput(), $utf8).ReadToEnd()
$cwd = [string](ConvertFrom-Json $inputText).cwd
if (-not $cwd) { exit 0 }

# Config: MEMORY_CURATION_CONFIG, else config.json in the repository root (one level above hooks/)
$configPath = if ($env:MEMORY_CURATION_CONFIG) { $env:MEMORY_CURATION_CONFIG } else { Join-Path (Split-Path $PSScriptRoot -Parent) 'config.json' }
$cfg = if (Test-Path -LiteralPath $configPath) { ConvertFrom-Json ([System.IO.File]::ReadAllText($configPath, $utf8)) } else { $null }
function Expand([string]$p) { if ($p.StartsWith('~')) { Join-Path $env:USERPROFILE $p.Substring(1).TrimStart('/', '\') } else { $p } }
$ws = if ($cfg -and $cfg.workspace) { Expand $cfg.workspace } else { exit 0 }      # no workspace configured: do nothing
$ws = [System.IO.Path]::GetFullPath($ws).TrimEnd('\')
$mainRoot = if ($cfg.roots -and $cfg.roots.main) { Expand $cfg.roots.main } else { Join-Path $env:USERPROFILE '.claude\projects\memories' }
$exclude = @($cfg.exclude | Where-Object { $_ })    # workspace-relative folders (and their children) that get no memory

$cwd = [System.IO.Path]::GetFullPath($cwd).TrimEnd('\')
if (-not $cwd.StartsWith($ws + '\', [System.StringComparison]::OrdinalIgnoreCase)) { exit 0 }
$rel = $cwd.Substring($ws.Length + 1)
foreach ($x in $exclude) {
    $x = $x.Replace('/', '\').Trim('\')
    if ($rel -eq $x -or $rel.StartsWith($x + '\', [System.StringComparison]::OrdinalIgnoreCase)) { exit 0 }
}

$settingsDir = Join-Path $cwd '.claude'
$settingsPath = Join-Path $settingsDir 'settings.local.json'
foreach ($name in 'settings.local.json', 'settings.json') {
    $f = Join-Path $settingsDir $name
    if (-not (Test-Path -LiteralPath $f)) { continue }
    $c = ConvertFrom-Json ([System.IO.File]::ReadAllText($f, $utf8))
    if ($c.autoMemoryDirectory -or $c.autoMemoryEnabled -eq $false) { exit 0 }
}

$wsName = Split-Path $ws -Leaf
$memdir = (Join-Path $mainRoot ($wsName + '__' + ($rel -replace '\\', '__'))).Replace('\', '/')
New-Item -ItemType Directory -Force (Join-Path $memdir 'distilled') | Out-Null

# Nearest ancestor folder with a memory root = shared memory pointer
$parentL0 = $null
$dir = Split-Path $cwd -Parent
while ($dir.Length -ge $ws.Length -and -not $parentL0) {
    foreach ($name in 'settings.local.json', 'settings.json') {
        $f = Join-Path $dir ".claude\$name"
        if (Test-Path -LiteralPath $f) {
            $m = (ConvertFrom-Json ([System.IO.File]::ReadAllText($f, $utf8))).autoMemoryDirectory
            if ($m) { $parentL0 = "$m/MEMORY.md"; break }
        }
    }
    $dir = Split-Path $dir -Parent
}

# A root that already exists (its folder setting was lost, or the folder was restored) is only reconnected, never rewritten
$existing = Test-Path -LiteralPath (Join-Path $memdir 'MEMORY.md')
if (-not $existing) {
    $today = Get-Date -Format 'yyyy-MM-dd'
    $l0 = "# Memory Index: $($rel.Replace('\', '/'))`n`nLast curated: $today (created by ensure-memory-dir hook).`n`n"
    if ($parentL0) { $l0 += "Shared memory (ways of working, tools, preferences): ``$parentL0```n`n" }
    $l0 += "## Hot`n- None.`n`n## Topic indexes`n- None.`n`n## Recent / pending curate`n- None.`n"
    [System.IO.File]::WriteAllText((Join-Path $memdir 'MEMORY.md'), $l0, $utf8)
    [System.IO.File]::WriteAllText((Join-Path $memdir 'log.md'), "- [$today] created | memory dir by ensure-memory-dir hook`n", $utf8)
}

New-Item -ItemType Directory -Force $settingsDir | Out-Null
$s = if (Test-Path -LiteralPath $settingsPath) { ConvertFrom-Json ([System.IO.File]::ReadAllText($settingsPath, $utf8)) } else { [pscustomobject]@{} }
$s | Add-Member -NotePropertyName autoMemoryDirectory -NotePropertyValue $memdir -Force
[System.IO.File]::WriteAllText($settingsPath, (($s | ConvertTo-Json -Depth 10) + "`n"), $utf8)

$what = if ($existing) { "Reconnected this folder to its existing memory root" } else { "Created this folder's memory root" }
$out = @{ hookSpecificOutput = @{ hookEventName = 'SessionStart'
    additionalContext = "${what}: $memdir`nSave this session's memories there (later sessions pick it up automatically)." } }
[Console]::OutputEncoding = $utf8
[Console]::Out.Write(($out | ConvertTo-Json -Depth 5 -Compress))

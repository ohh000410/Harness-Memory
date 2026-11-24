# SessionStart hook for harnesses without Claude Code's autoMemoryDirectory (Codex; agy through agy-session-start.ps1):
# puts the folder's MEMORY.md into the session, so they start from the same memory Claude Code loads by itself.
# Same rule as Claude Code: only the folder the session opened in is looked at (no walk up to parent folders) —
# a sub-folder root reaches its parent's memory through the pointer line ensure-memory-dir.ps1 writes into MEMORY.md.
# Register it after ensure-memory-dir.ps1 so the first session in a folder already sees the new root.
$utf8 = [System.Text.UTF8Encoding]::new($false)
$inputText = [System.IO.StreamReader]::new([Console]::OpenStandardInput(), $utf8).ReadToEnd()
$cwd = [string](ConvertFrom-Json $inputText).cwd
if (-not $cwd) { exit 0 }

$memdir = $null
foreach ($name in 'settings.local.json', 'settings.json') {
    $f = Join-Path $cwd ".claude\$name"
    if (-not (Test-Path -LiteralPath $f)) { continue }
    $c = ConvertFrom-Json ([System.IO.File]::ReadAllText($f, $utf8))
    if ($c.autoMemoryDirectory) { $memdir = [string]$c.autoMemoryDirectory; break }
}
if (-not $memdir) { exit 0 }
$l0 = Join-Path $memdir 'MEMORY.md'
if (-not (Test-Path -LiteralPath $l0)) { exit 0 }

$text = "# Memory index (injected at session start)`n`nMemory directory: $memdir`n`n" + [System.IO.File]::ReadAllText($l0, $utf8)
$out = @{ hookSpecificOutput = @{ hookEventName = 'SessionStart'; additionalContext = $text } }
[Console]::OutputEncoding = $utf8
[Console]::Out.Write(($out | ConvertTo-Json -Depth 5 -Compress))

# SessionStart hook for harnesses without Claude Code's autoMemoryDirectory (Codex; agy through agy-session-start.ps1).
# 1. Runs ensure-memory-dir.ps1 first, inside this hook: Codex starts the hooks of one event together, so a separate
#    ensure hook registered before this one has not finished when this one reads the folder setting, and the first session
#    in a new folder got no index (seen with Codex 0.169).
# 2. Puts the folder's MEMORY.md into the session, so these harnesses start from the same memory Claude Code loads by itself.
# Same rule as Claude Code: only the folder the session opened in is looked at — a sub-folder root reaches its parent's
# memory through the pointer line ensure-memory-dir.ps1 writes into MEMORY.md.
$ErrorActionPreference = 'Stop'   # a broken setting fails loudly instead of injecting nothing
$utf8 = [System.Text.UTF8Encoding]::new($false)
$inputText = [System.IO.StreamReader]::new([Console]::OpenStandardInput(), $utf8).ReadToEnd()
$cwd = [string](ConvertFrom-Json $inputText).cwd
if (-not $cwd) { exit 0 }

$psi = [System.Diagnostics.ProcessStartInfo]::new('powershell.exe', "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'ensure-memory-dir.ps1')`"")
$psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.UseShellExecute = $false
$psi.StandardOutputEncoding = $utf8; $psi.StandardErrorEncoding = $utf8
$p = [System.Diagnostics.Process]::Start($psi)
$w = [System.IO.StreamWriter]::new($p.StandardInput.BaseStream, $utf8); $w.Write($inputText); $w.Close()
$ensureOut = $p.StandardOutput.ReadToEnd(); $ensureErr = $p.StandardError.ReadToEnd(); $p.WaitForExit()
if ($p.ExitCode -ne 0) { [Console]::Error.Write($ensureErr); exit $p.ExitCode }
$parts = @()
if ($ensureOut.Trim()) { $parts += [string]($ensureOut | ConvertFrom-Json).hookSpecificOutput.additionalContext }

$memdir = $null
foreach ($name in 'settings.local.json', 'settings.json') {
    $f = Join-Path $cwd ".claude\$name"
    if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { continue }
    $c = ConvertFrom-Json ([System.IO.File]::ReadAllText($f, $utf8))
    if ($c.autoMemoryEnabled -eq $false) { exit 0 }   # memory turned off for this folder, as Claude Code would treat it
    if (-not $memdir -and $c.autoMemoryDirectory) { $memdir = [string]$c.autoMemoryDirectory }
}
if ($memdir) {
    # a hand-written setting may start with ~ — .NET file calls do not expand it (Test-Path would, ReadAllText would not)
    if ($memdir -match '^~([\\/]|$)') { $memdir = Join-Path $env:USERPROFILE ($memdir.Substring(1).TrimStart('/', '\')) }
    $l0 = Join-Path $memdir 'MEMORY.md'
    if (Test-Path -LiteralPath $l0 -PathType Leaf) {
        $parts += "# Memory index (injected at session start)`n`nMemory directory: $memdir`n`n" + [System.IO.File]::ReadAllText($l0, $utf8)
    }
}
if (-not $parts) { exit 0 }
$out = @{ hookSpecificOutput = @{ hookEventName = 'SessionStart'; additionalContext = ($parts -join "`n`n---`n`n") } }
[Console]::OutputEncoding = $utf8
[Console]::Out.Write(($out | ConvertTo-Json -Depth 5 -Compress))

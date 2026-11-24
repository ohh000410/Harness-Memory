# Antigravity CLI (agy) hook. agy has no SessionStart event, so this runs on PreInvocation (every model call) and acts only
# on the first call of a conversation (initialNumSteps 1, invocationNum 0): it runs ensure-memory-dir.ps1 and
# inject-memory-index.ps1 — the same pair Codex runs at SessionStart — and injects their text as a user message.
# A userMessage stays in the conversation; an ephemeralMessage would be gone on the next turn (agy 1.3.1).
$utf8 = [System.Text.UTF8Encoding]::new($false)
$inputText = [System.IO.StreamReader]::new([Console]::OpenStandardInput(), $utf8).ReadToEnd()
$hookInput = $inputText | ConvertFrom-Json
[Console]::OutputEncoding = $utf8
if ($hookInput.initialNumSteps -ne 1 -or $hookInput.invocationNum -ne 0) { [Console]::Out.Write('{}'); exit 0 }

# agy's input has no cwd; the workspace folder is the first of workspacePaths
$cwd = ([string]@($hookInput.workspacePaths)[0]) -replace '/', '\'
if (-not $cwd) { [Console]::Out.Write('{}'); exit 0 }
$childInput = @{ cwd = $cwd; hook_event_name = 'SessionStart'; source = 'startup' } | ConvertTo-Json -Compress
$parts = foreach ($name in 'ensure-memory-dir.ps1', 'inject-memory-index.ps1') {
    $psi = [System.Diagnostics.ProcessStartInfo]::new('powershell.exe', "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot $name)`"")
    $psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.UseShellExecute = $false
    $psi.StandardOutputEncoding = $utf8
    $p = [System.Diagnostics.Process]::Start($psi)
    $w = [System.IO.StreamWriter]::new($p.StandardInput.BaseStream, $utf8); $w.Write($childInput); $w.Close()
    $out = $p.StandardOutput.ReadToEnd(); $p.WaitForExit()
    if ($out.Trim()) { [string]($out | ConvertFrom-Json).hookSpecificOutput.additionalContext }
}
$text = (@($parts) | Where-Object { $_ }) -join "`n`n---`n`n"
if (-not $text) { [Console]::Out.Write('{}'); exit 0 }
[Console]::Out.Write((@{ injectSteps = @(@{ userMessage = $text }) } | ConvertTo-Json -Depth 5 -Compress))

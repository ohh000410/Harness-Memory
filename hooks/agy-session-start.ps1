# Antigravity CLI (agy) SessionStart hook (agy 1.5.16: runs once when a conversation starts). Runs inject-memory-index.ps1 —
# the hook Codex uses, which creates the folder's memory root and returns its MEMORY.md — and hands that text to agy as a
# system message, which stays in the conversation across turns. agy expects injectSteps on stdout, not Claude Code's
# hookSpecificOutput, hence this small adapter.
$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.UTF8Encoding]::new($false)
$inputText = [System.IO.StreamReader]::new([Console]::OpenStandardInput(), $utf8).ReadToEnd()
$cwd = [string]($inputText | ConvertFrom-Json).cwd
[Console]::OutputEncoding = $utf8
if (-not $cwd) { [Console]::Out.Write('{}'); exit 0 }

$childInput = @{ cwd = ($cwd -replace '/', '\'); hook_event_name = 'SessionStart'; source = 'startup' } | ConvertTo-Json -Compress
$psi = [System.Diagnostics.ProcessStartInfo]::new('powershell.exe', "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'inject-memory-index.ps1')`"")
$psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.UseShellExecute = $false
$psi.StandardOutputEncoding = $utf8; $psi.StandardErrorEncoding = $utf8
$p = [System.Diagnostics.Process]::Start($psi)
$w = [System.IO.StreamWriter]::new($p.StandardInput.BaseStream, $utf8); $w.Write($childInput); $w.Close()
$out = $p.StandardOutput.ReadToEnd(); $err = $p.StandardError.ReadToEnd(); $p.WaitForExit()
if ($p.ExitCode -ne 0) { [Console]::Error.Write($err); exit $p.ExitCode }
if (-not $out.Trim()) { [Console]::Out.Write('{}'); exit 0 }
$text = [string]($out | ConvertFrom-Json).hookSpecificOutput.additionalContext
[Console]::Out.Write((@{ injectSteps = @(@{ systemMessage = @{ systemMessage = $text } }) } | ConvertTo-Json -Depth 6 -Compress))

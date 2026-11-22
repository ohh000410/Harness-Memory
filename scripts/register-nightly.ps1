# Register a daily Task Scheduler job that runs curate.py (default 03:00). Re-run to update; -Remove to delete.
param([string]$At = '03:00', [string]$Python = 'python', [switch]$Remove)
$name = 'curate-memory'
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; return }
$script = Join-Path (Split-Path $PSScriptRoot -Parent) 'skills\curate-memory\scripts\curate.py'
$action = New-ScheduledTaskAction -Execute $Python -Argument "-X utf8 `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
"registered $name daily at $At -> $script"

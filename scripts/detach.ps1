# Start a command fully detached from the caller and return at once.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\detach.ps1 scripts\holmes_nightly.cmd
#
# `start /b`, and Start-Process with -Redirect*, hand the caller's console/pipe handles to the
# child, so a tool that waits for its command (an agent shell, a CI step) blocks until the
# child exits. A Win32_Process.Create launch returned at once but its job was killed by a
# Ctrl+C a few seconds later. Start-Process in a new hidden window, with no redirection
# (the command writes its own logs), returns immediately and survives the caller.
param(
    [Parameter(Mandatory = $true)][string]$Command
)
$cwd = (Get-Location).Path
$p = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $Command `
    -WorkingDirectory $cwd -WindowStyle Hidden -PassThru
"started pid=$($p.Id) in $cwd"

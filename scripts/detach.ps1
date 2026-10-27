# Start a command fully detached from the caller and return at once.
#
#   powershell -NoProfile -File scripts\detach.ps1 scripts\holmes_nightly.cmd
#
# `start /b` and `Start-Process` keep the child attached to the caller's console handles, so a
# tool that waits for its command to finish (an agent shell, a CI step) blocks until the child
# exits. Win32_Process.Create makes a process with no parent console; the caller gets the PID.
param(
    [Parameter(Mandatory = $true)][string]$Command
)
$cwd = (Get-Location).Path
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine      = "cmd /c $Command"
    CurrentDirectory = $cwd
}
if ($r.ReturnValue -ne 0) { Write-Error "Win32_Process.Create returned $($r.ReturnValue)"; exit 1 }
"started pid=$($r.ProcessId) in $cwd"

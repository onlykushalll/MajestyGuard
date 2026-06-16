param(
    [string]$TaskName = "MajestyGuard_UserDaemon"
)

$ErrorActionPreference = "Stop"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed user startup task: $TaskName"
} else {
    Write-Host "Startup task not installed: $TaskName"
}

$StartupDir = [Environment]::GetFolderPath("Startup")
$StartupCommandName = "MajestyGuard_UserDaemon.cmd"
if ($TaskName -ne "MajestyGuard_UserDaemon") {
    $StartupCommandName = "$TaskName.cmd"
}
$StartupCommand = Join-Path $StartupDir $StartupCommandName
if (Test-Path $StartupCommand) {
    Remove-Item -LiteralPath $StartupCommand -Force
    Write-Host "Removed Startup folder fallback: $StartupCommand"
}

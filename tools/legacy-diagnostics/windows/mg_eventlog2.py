import subprocess
# Get ALL recent errors including from AppModel/Windows logs
r = subprocess.run([
    "powershell", "-ExecutionPolicy", "Bypass", "-Command",
    "$logs = 'Application','System'; "
    "foreach ($log in $logs) { "
    "  try { "
    "    Get-WinEvent -LogName $log -MaxEvents 50 -ErrorAction Stop | "
    "    Where-Object { $_.TimeCreated -gt (Get-Date).AddMinutes(-5) } | "
    "    Select-Object TimeCreated,LevelDisplayName,ProviderName,Message | "
    "    Format-List "
    "  } catch {} "
    "}"
], capture_output=True, text=True, timeout=30)
out = r.stdout.strip()
print(out[:5000] if out else "NO EVENTS IN LAST 5 MINUTES")

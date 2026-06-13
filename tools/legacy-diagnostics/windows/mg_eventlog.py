import subprocess
r = subprocess.run([
    "powershell", "-ExecutionPolicy", "Bypass", "-Command",
    "Get-WinEvent -LogName Application -MaxEvents 30 | "
    "Where-Object { $_.LevelDisplayName -eq 'Error' } | "
    "Select-Object -First 5 TimeCreated,Message | "
    "Format-List"
], capture_output=True, text=True)
print(r.stdout[:4000] if r.stdout else "(empty)")
if r.stderr: print("ERR:", r.stderr[:500])

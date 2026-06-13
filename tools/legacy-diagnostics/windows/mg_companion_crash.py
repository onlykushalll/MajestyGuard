import subprocess
result = subprocess.run([
    "powershell", "-ExecutionPolicy", "Bypass", "-Command",
    "Get-WinEvent -LogName 'Microsoft-Windows-AppxPackagingOM/Microsoft Windows/AppxPackagingOM/Microsoft-Windows-AppxPackagingOM' -MaxEvents 5 -ErrorAction SilentlyContinue | Select-Object TimeCreated,Message | Format-List; "
    "Get-AppPackageLog -ActivityID (Get-AppxLog | Select-Object -First 1 -ExpandProperty ActivityId) -ErrorAction SilentlyContinue; "
    "Get-WinEvent -LogName Application -MaxEvents 20 | Where-Object {$_.Message -like '*MajestyGuard*' -or $_.Message -like '*AppxPackage*'} | Select-Object TimeCreated,Message | Format-List"
], capture_output=True, text=True)
print(result.stdout[:3000] if result.stdout else "(no output)")
if result.stderr: print("ERR:", result.stderr[:500])

# Wake Windows screen via DLL import
# Usage: powershell.exe -ExecutionPolicy Bypass -File "C:\Users\<user>\WakeScreen.ps1"

Add-Type -MemberDefinition '[DllImport("user32.dll")] public static extern int SendMessage(int hWnd, int hMsg, int wParam, int lParam);' -Name 'Win32SendMessage' -Namespace 'Win32' -PassThru

# Parameter 1 = wake screen
$result = [Win32.Win32SendMessage]::SendMessage(0xffff, 0x0112, 0xf170, 1)
Write-Host "Screen wake sent (result: $result)"
exit 0

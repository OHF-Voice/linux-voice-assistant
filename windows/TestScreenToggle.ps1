# Test script to toggle screen on/off every 5 seconds
# Save as: C:\Users\<username>\TestScreenToggle.ps1
#
# EXECUTION COMMAND (copy-paste this):
# powershell.exe -ExecutionPolicy Bypass -File "C:\Users\<username>\TestScreenToggle.ps1"
#
# Replace <username> with your Windows username

Add-Type -MemberDefinition '[DllImport("user32.dll")] public static extern int SendMessage(int hWnd, int hMsg, int wParam, int lParam);' -Name 'Win32SendMessage' -Namespace 'Win32' -PassThru

Write-Host "Testing screen wake/sleep toggle..."
Write-Host "Watch your monitor for sleep/wake."

for ($i = 1; $i -le 5; $i++) {
    Write-Host "Cycle $i - Sending SLEEP (parameter 2)..."
    [Win32.Win32SendMessage]::SendMessage(0xffff, 0x0112, 0xf170, 2)
    Start-Sleep -Seconds 5
    
    Write-Host "Cycle $i - Sending WAKE (parameter 1)..."
    [Win32.Win32SendMessage]::SendMessage(0xffff, 0x0112, 0xf170, 1)
    Start-Sleep -Seconds 5
}

Write-Host "Test complete!"

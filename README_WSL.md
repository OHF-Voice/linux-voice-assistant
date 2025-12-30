🚀 WSL2 Ubuntu LVA Setup Summary
1. Enable Systemd (The Background Service Brain)
This allows your Linux system to manage the 5 modular services.

Edit Config: sudo nano /etc/wsl.conf

Add these lines:

Ini, TOML

[boot]
systemd=true
Restart (In Windows PowerShell): wsl --shutdown

2. Mirrored Networking (The LAN Bridge)
This makes your WSL Ubuntu share your Windows IP so Home Assistant can find it.

Create/Edit File (In Windows PowerShell): notepad "$HOME\.wslconfig"

Add these lines:

Ini, TOML

[wsl2]
networkingMode=mirrored
firewall=true
Restart (In Windows PowerShell): wsl --shutdown

3. Windows Firewall Rule (Opening the Gate)
Since we moved to port 6070 to avoid Windows port conflicts, we must tell Windows to let that traffic through.

Run in Windows PowerShell (Admin):

PowerShell

New-NetFirewallRule -DisplayName "LVA-Test-Port" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 6070
4. Running the Engine
Go to your folder and run the engine on the "clean" port we opened.

Run in Ubuntu Terminal:

Bash

cd ~/linux-voice-assistant
script/run --name "test" --port 6070 --debug
5. Home Assistant Connection
When adding the ESPHome integration in Home Assistant:

Host: Use your PC's IP (e.g., 192.168.1.71) or 127.0.0.1

Port: 6070

Encryption Key: Leave blank.

6. Helpful Maintenance Commands
Check your current Linux IP: hostname -I

Verify if Systemd is active: systemctl is-system-running

List all active WSL distros: wsl -l -v
# Systemd Service Files (Docker)

These are **optional** example systemd service files for running the Linux Voice Assistant **with Docker**. If you're running the application directly (without Docker), see the main [README.md](../README.md).

## Do I Need These?

- **Standard users (non-root):** Probably **NO**. Docker's `restart: unless-stopped` in docker-compose.yml handles auto-start. PulseAudio runs automatically as part of your user session.

- **Root users:** **YES**. You need systemd to start PulseAudio before Docker, since there's no user session to auto-start it.

## Installation

### For Standard Users (non-root)

**You likely don't need systemd at all.** The default `restart: unless-stopped` in docker-compose.yml will auto-start the container on boot.

Simply run once:
```bash
docker compose up -d
```

The container will automatically restart after reboots.

**Optional:** If you prefer systemd for management (viewing logs with `journalctl`, etc.):

```bash
# Copy the container service
sudo cp linux-voice-assistant.service.example /etc/systemd/system/linux-voice-assistant.service

# Edit the service file to set your username and project path
sudo nano /etc/systemd/system/linux-voice-assistant.service

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable linux-voice-assistant.service
sudo systemctl start linux-voice-assistant.service
```

### For Root Users

Running as root **requires** systemd because PulseAudio doesn't auto-start without a user session.

1. **Edit docker-compose.yml** - Comment out `restart: unless-stopped` and uncomment `restart: "no"`

2. **Install both services:**
   ```bash
   # Copy both services
   sudo cp pulseaudio-root.service.example /etc/systemd/system/pulseaudio-root.service
   sudo cp linux-voice-assistant.service.example /etc/systemd/system/linux-voice-assistant.service

   # Edit linux-voice-assistant.service:
   # - Change WorkingDirectory to your project path
   sudo nano /etc/systemd/system/linux-voice-assistant.service

   # Enable and start
   sudo systemctl daemon-reload
   sudo systemctl enable pulseaudio-root.service linux-voice-assistant.service
   sudo systemctl start pulseaudio-root.service
   sudo systemctl start linux-voice-assistant.service
   ```

3. **Update .env** - Set `PULSE_RUNTIME_PATH=/var/run/pulse`

## Checking Status

```bash
# View service status
sudo systemctl status linux-voice-assistant.service

# View logs
sudo journalctl -u linux-voice-assistant.service -f

# For root users, also check PulseAudio
sudo systemctl status pulseaudio-root.service
sudo journalctl -u pulseaudio-root.service -f
```

## Troubleshooting

### Container starts before PulseAudio is ready

The `docker-entrypoint.sh` script waits up to 60 seconds for PulseAudio. If you're still seeing issues:

1. Check PulseAudio is running: `pactl info`
2. Check the socket exists: `ls -la /run/user/$(id -u)/pulse/native` (or `/var/run/pulse/native` for root)
3. Verify `PULSE_RUNTIME_PATH` in `.env` matches the actual socket location

### Service fails after reboot

For root users: The `/run/user/0` directory is managed by `systemd-logind` and gets recreated on SSH login, which can wipe PulseAudio sockets. Use `/var/run/pulse` instead (configured in `pulseaudio-root.service.example`).

# Docker Setup for Linux Voice Assistant

This document explains how to run the Linux Voice Assistant using Docker and Docker Compose.

## Prerequisites

- Docker and Docker Compose installed on your system
- Audio devices (microphone and speakers) available
- PulseAudio running on the host (see [PulseAudio Setup](#pulseaudio-setup))

## Quick Start

1. **Clone the repository**:
   ```bash
   git clone https://github.com/OHF-Voice/linux-voice-assistant.git
   cd linux-voice-assistant
   ```

2. **Configure environment**:
   ```bash
   cp .env.example .env
   nano .env  # Edit to match your system
   ```
   
   Key settings to configure:
   - `PULSE_RUNTIME_PATH`: Path to your PulseAudio socket
   - `LVA_MAC_ADDRESS`: Your host's MAC address (for consistent HA device identity)
   - `LVA_NAME`: Name shown in Home Assistant

3. **Build and run**:
   ```bash
   docker compose up --build -d
   ```

4. **Check logs**:
   ```bash
   docker compose logs -f
   ```

## Configuration

### Environment Variables

Copy and customize the example environment file:
```bash
cp .env.example .env
```

| Variable | Description | Default |
|----------|-------------|---------|
| `PULSE_RUNTIME_PATH` | PulseAudio socket directory | `/run/user/1000/pulse` |
| `LVA_NAME` | Device name in Home Assistant | `Linux Voice Assistant` |
| `LVA_MAC_ADDRESS` | Host MAC for consistent HA identity | (auto-detect) |
| `LVA_WAKE_MODEL` | Wake word model | `okay_nabu` |
| `LVA_DEBUG` | Enable debug logging | `false` |

**Finding your settings:**
```bash
# Get your user ID (for PULSE_RUNTIME_PATH)
id -u

# Get your MAC address (for LVA_MAC_ADDRESS)
ip link show | grep ether | head -1 | awk '{print $2}'
```

### PulseAudio Setup

The voice assistant requires PulseAudio for audio I/O.

#### Standard Users (non-root)

PulseAudio usually runs automatically. Verify with:
```bash
pactl info
```

Your socket is typically at `/run/user/$(id -u)/pulse`. Set in `.env`:
```
PULSE_RUNTIME_PATH=/run/user/1000/pulse
```

#### Root Users

Running as root requires a separate PulseAudio service since there's no user session.

1. Use the provided systemd service:
   ```bash
   sudo cp systemd/pulseaudio-root.service.example /etc/systemd/system/pulseaudio-root.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now pulseaudio-root.service
   ```

2. Set in `.env`:
   ```
   PULSE_RUNTIME_PATH=/var/run/pulse
   ```

See [systemd/README.md](systemd/README.md) for more details.

### Wake Word Models

The container includes several pre-trained wake word models:
- `okay_nabu` (default)
- `alexa`
- `hey_jarvis`
- `hey_mycroft`
- `hey_luna`
- `okay_computer`

To use a different wake word, set `LVA_WAKE_MODEL` in your `.env` file.

#### Adding Custom Wake Words

1. Create a `custom_wakewords` directory in your project root
2. Add your `.tflite` model files and corresponding `.json` config files
3. Uncomment the custom_wakewords volume mount in `docker-compose.yaml`

## Auto-Start on Boot (Optional)

To have the voice assistant start automatically on boot, use the provided systemd service files.

See [systemd/README.md](systemd/README.md) for installation instructions.

**Summary:**
- Standard users: Install only `linux-voice-assistant.service`
- Root users: Install both `pulseaudio-root.service` and `linux-voice-assistant.service`

## Usage Commands

### Basic Operations

```bash
# Start the service
docker compose up -d

# View logs
docker compose logs -f

# Stop the service
docker compose down

# Rebuild after code changes
docker compose up --build -d

# Check service status
docker compose ps
```

### Development

```bash
# Execute commands in running container
docker compose exec linux-voice-assistant bash

# View real-time logs with timestamps
docker compose logs -f -t linux-voice-assistant
```

## Troubleshooting

### Audio Issues

1. **No audio input detected**:
   - Check if your user is in the `audio` group: `groups $USER`
   - Add user to audio group: `sudo usermod -a -G audio $USER`
   - Restart your session after adding to the group

2. **PulseAudio connection issues**:
   - Verify PulseAudio is running: `pactl info`
   - Check socket exists: `ls -la ${PULSE_RUNTIME_PATH}/native`
   - For root users, ensure `pulseaudio-root.service` is running
   - Check `PULSE_RUNTIME_PATH` in `.env` matches actual socket location

3. **Container starts before PulseAudio is ready**:
   - The entrypoint script waits up to 60 seconds for PulseAudio
   - If using systemd, ensure proper service ordering (see `systemd/README.md`)
   - For root users on reboot: use `/var/run/pulse` not `/run/user/0/pulse`

### Network Issues

1. **Service not discoverable by Home Assistant**:
   - Ensure host networking is enabled (`network_mode: host` in docker-compose.yaml)
   - Check if port 6053 is available: `ss -tln | grep 6053`
   - Verify zeroconf/mDNS is working: `avahi-browse -a`

2. **Home Assistant sees duplicate devices**:
   - Set `LVA_MAC_ADDRESS` in `.env` to your host's MAC address
   - This ensures consistent device identity across container restarts

### Container Issues

1. **Build failures**:
   - Clear Docker cache: `docker system prune -a`
   - Check available disk space
   - Ensure all dependencies are available

2. **Runtime errors**:
   - Check container logs: `docker compose logs linux-voice-assistant`
   - Verify all volume mounts exist and have correct permissions
   - Enable debug logging: set `LVA_DEBUG=true` in `.env`

## Integration with Home Assistant

The voice assistant uses the ESPHome protocol and should automatically be discovered by Home Assistant through zeroconf/mDNS. 

1. Ensure both the voice assistant and Home Assistant are on the same network
2. In Home Assistant, go to Settings → Devices & Services
3. Look for the automatically discovered ESPHome device
4. Follow the integration setup process

## Security Considerations

- The container runs with minimal privileges (no `--privileged` flag)
- Only necessary capabilities are added (`SYS_NICE` for audio priority)
- Audio devices are mounted read-write only as needed

## Support

For issues specific to the Docker setup:
1. Check this documentation and [systemd/README.md](systemd/README.md)
2. Review Docker Compose logs: `docker compose logs`
3. See the main [README.md](README.md) for general voice assistant issues
4. Open an issue on GitHub

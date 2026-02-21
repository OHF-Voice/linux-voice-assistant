FROM python:3.13-slim-trixie

ENV LANG C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

LABEL \
    org.opencontainers.image.authors="Open Home Foundation" \
    org.opencontainers.image.description="Voice assistant for Home Assistant" \
    org.opencontainers.image.documentation="https://github.com/OHF-Voice/linux-voice-assistant/blob/main/README.md" \
    org.opencontainers.image.licenses="Apache-2.0" \
    org.opencontainers.image.source="https://github.com/OHF-Voice/linux-voice-assistant" \
    org.opencontainers.image.title="Linux-Voice-Assistant" \
    org.opencontainers.image.url="https://github.com/OHF-Voice/linux-voice-assistant"

### Install packages:
# - avahi-utils:        For zeroconf/mDNS discovery by Home Assistant
# - pulseaudio-utils:   Required by soundcard library for audio I/O
# - alsa-utils:         ALSA tools for audio device management
# - pipewire-bin:       Required for pipewire support
# - pipewire-alsa:      Required for pipewire support
# - pipewire-pulse:     Required for pipewire support
# - build-essential:    Required to compile pymicro-features
# - libmpv-dev:         Required by python-mpv for audio playback
# - libasound2-plugins: Required by python-mpv for audio playback
# - ca-certificates:    For encrypted connections
# - iproute2:           For ss command in entrypoint (port check)
# - procps:             For pgrep in healthcheck
RUN apt-get update && \
apt-get install --yes --no-install-recommends \
    avahi-utils \
    pulseaudio-utils \
    alsa-utils \
    pipewire-bin \
    pipewire-alsa \
    pipewire-pulse \
    build-essential \
    libmpv-dev \
    libasound2-plugins \
    ca-certificates \
    iproute2 \
    vim \
    procps && \
apt-get clean

### Set workdir:
WORKDIR /app

### Copy all application files:
COPY script/ ./script/
COPY pyproject.toml ./
COPY setup.cfg ./
COPY sounds/ ./sounds/
COPY ../wakewords/ ./wakewords/
COPY linux_voice_assistant/ ./linux_voice_assistant/
COPY docker-entrypoint.sh ./
COPY version.txt ./
COPY version_githash.txt ./

### Run installation:
RUN chmod +x docker-entrypoint.sh
RUN ./script/setup

### Set ports for ESPHome API:
EXPOSE 6053

### Set start script:
ENTRYPOINT ["./docker-entrypoint.sh"]

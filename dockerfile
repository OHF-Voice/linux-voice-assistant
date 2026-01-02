FROM python:3.13-slim-trixie

ENV LANG=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
# - pulseaudio: Required by soundcard library for audio I/O
# - alsa-utils: ALSA tools for audio device management
# - avahi-utils: For zeroconf/mDNS discovery by Home Assistant
# - build-essential: Required to compile pymicro-features
# - libmpv-dev: Required by python-mpv for audio playback
# - pipewire/pipewire-pulse: Required by mpv for audio output
# - iproute2: For ss command in entrypoint (port check)
# - procps: For pgrep in healthcheck
RUN apt-get update && \
    apt-get install --yes --no-install-recommends \
    pulseaudio \
    pulseaudio-utils \
    pipewire \
    pipewire-pulse \
    alsa-utils \
    avahi-utils \
    build-essential \
    libmpv-dev \
    iproute2 \
    procps \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create PipeWire config directory to suppress warnings
RUN mkdir -p /etc/pipewire /run/pulse \
    && chmod 755 /run/pulse

WORKDIR /srv
COPY pyproject.toml setup.cfg ./
COPY script/ ./script/
COPY linux_voice_assistant/ ./linux_voice_assistant/
COPY wakewords/ ./wakewords/
COPY sounds/ ./sounds/
COPY docker-entrypoint.sh /usr/local/bin/

# Install Python dependencies (no venv needed in container)
RUN pip install --no-cache-dir -e . && \
    chmod +x /usr/local/bin/docker-entrypoint.sh

# Create directory for downloaded wake words
RUN mkdir -p /srv/local

EXPOSE 6053

# Use entrypoint script that waits for PulseAudio
ENTRYPOINT ["docker-entrypoint.sh"]

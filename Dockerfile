### Build stage: compile the Python dependencies (pymicro-features needs a compiler)
FROM python:3.13-slim-trixie AS build

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install --yes --no-install-recommends build-essential && \
    apt-get clean

WORKDIR /app

COPY pyproject.toml setup.cfg version.txt version_githash.txt ./
COPY linux_voice_assistant/ ./linux_voice_assistant/

RUN python -m venv .venv && \
    .venv/bin/pip install --no-cache-dir . && \
    .venv/bin/pip uninstall --yes pip setuptools wheel && \
    find .venv -name '__pycache__' -prune -exec rm -rf {} +

### Runtime stage
FROM python:3.13-slim-trixie

ENV LANG C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

ARG VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${VERSION}

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
# - libmpv2:            Runtime library for python-mpv (no -dev package, no build tools)
# - libasound2-plugins: Required by python-mpv for audio playback
# - ca-certificates:    For encrypted connections
# - iproute2:           For ss command in entrypoint (port check)
# - vim:                Editor for debugging inside the container
# - procps:             For pgrep in healthcheck
#
# libmpv2 depends on libegl1/libgbm1, which pull in Mesa and LLVM (~170 MB).
# LVA only plays audio, so these are purged without their dependents.
RUN apt-get update && \
    apt-get install --yes --no-install-recommends \
    avahi-utils \
    pulseaudio-utils \
    alsa-utils \
    pipewire-bin \
    pipewire-alsa \
    pipewire-pulse \
    libmpv2 \
    libasound2-plugins \
    ca-certificates \
    iproute2 \
    vim \
    procps && \
    dpkg --purge --force-depends libllvm19 mesa-libgallium libgl1-mesa-dri libz3-4 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man /usr/share/locale

### Set workdir:
WORKDIR /app

### Copy virtual environment and application files:
COPY --from=build /app/.venv ./.venv
COPY script/ ./script/
COPY pyproject.toml ./
COPY setup.cfg ./
COPY sounds/ ./sounds/
COPY wakewords/ ./wakewords/
COPY linux_voice_assistant/ ./linux_voice_assistant/
COPY docker-entrypoint.sh ./
COPY version.txt ./
COPY version_githash.txt ./

RUN chmod +x docker-entrypoint.sh

### Set ports for ESPHome API:
EXPOSE 6053

### Set start script:
ENTRYPOINT ["./docker-entrypoint.sh"]

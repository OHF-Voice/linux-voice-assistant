### Build stage: compile the Python dependencies (pymicro-features needs a compiler)
# On linux/arm/v7 the dependencies without wheels (NumPy, cffi, webrtc-noise-gain, ...) are built from source,
# NumPy against OpenBLAS.
FROM python:3.13-slim-trixie AS build

ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install --yes --no-install-recommends build-essential && \
    if [ "$TARGETARCH" = "arm" ]; then \
        apt-get install --yes --no-install-recommends pkg-config libopenblas-dev libffi-dev; \
    fi && \
    apt-get clean

# linux/arm/v7 only: webrtc-noise-gain has no wheel and its setup.py breaks with setuptools >= 83 ("run() must precede build_extension()").
# PIP_BUILD_CONSTRAINT (pip >= 25.3) applies to pip's isolated build environments; PIP_CONSTRAINT no longer does.
RUN touch /tmp/constraints.txt && \
    if [ "$TARGETARCH" = "arm" ]; then echo 'setuptools<83' > /tmp/constraints.txt; fi
ENV PIP_BUILD_CONSTRAINT=/tmp/constraints.txt

WORKDIR /app

COPY pyproject.toml setup.cfg version.txt version_githash.txt ./
COPY linux_voice_assistant/ ./linux_voice_assistant/

RUN python -m venv .venv && \
    NO_BINARY="" && \
    if [ "$TARGETARCH" = "arm" ]; then NO_BINARY="--no-binary numpy"; fi && \
    .venv/bin/pip install --no-cache-dir $NO_BINARY .

# linux/arm/v7 only: pyopen-wakeword has no wheel there, so its sdist build bundles the x86-64 libtensorflowlite_c.so, which fails to load.
# Replace it with the armv7l library from the pymicro-wakeword wheel and check that it loads (the build stage runs under QEMU).
RUN if [ "$TARGETARCH" = "arm" ]; then \
        .venv/bin/python -c "\
import ctypes, pathlib, shutil, pymicro_wakeword, pyopen_wakeword; \
src = pathlib.Path(pymicro_wakeword.__file__).parent / 'lib' / 'libtensorflowlite_c.so'; \
dst = pathlib.Path(pyopen_wakeword.__file__).parent / 'lib' / 'libtensorflowlite_c.so'; \
shutil.copy(src, dst); \
ctypes.CDLL(str(dst))"; \
    fi && \
    .venv/bin/pip uninstall --yes pip setuptools wheel && \
    find .venv -name '__pycache__' -prune -exec rm -rf {} +

### Runtime stage
FROM python:3.13-slim-trixie

ARG TARGETARCH

ENV LANG C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
# The container runs as a user without a home directory (HOME=/). Without a writable cache directory,
# Fontconfig (loaded through libmpv, for example by libass) logs "Fontconfig error: No writable cache directories" every time it starts.
ENV XDG_CACHE_HOME=/tmp/.cache

LABEL \
    org.opencontainers.image.authors="Open Home Foundation" \
    org.opencontainers.image.description="Linux-Voice-Assistant for Home Assistant" \
    org.opencontainers.image.documentation="https://github.com/OHF-Voice/linux-voice-assistant/blob/main/README.md" \
    org.opencontainers.image.licenses="Apache-2.0" \
    org.opencontainers.image.source="https://github.com/OHF-Voice/linux-voice-assistant" \
    org.opencontainers.image.title="Linux-Voice-Assistant" \
    org.opencontainers.image.url="https://github.com/OHF-Voice/linux-voice-assistant" \
    org.opencontainers.image.vendor="Open Home Foundation"

### Install packages:
# - avahi-utils:        For zeroconf/mDNS discovery by Home Assistant
# - pulseaudio-utils:   Required by soundcard library for audio I/O
# - alsa-utils:         ALSA tools for audio device management
# - pipewire-bin:       Required for pipewire support
# - pipewire-alsa:      Required for pipewire support
# - pipewire-pulse:     Required for pipewire support
# - libmpv2:            Runtime library for python-mpv (no -dev package, no build tools)
# - libasound2-plugins: Required by python-mpv for audio playback
# - libopenblas0:       BLAS runtime for NumPy (linux/arm/v7 only, NumPy is built from source there)
# - ca-certificates:    For encrypted connections
# - iproute2:           For ss command in entrypoint (port check)
# - vim:                Editor for debugging inside the container
# - procps:             For pgrep in healthcheck
#
# libmpv2 depends on libegl1/libgbm1, which pull in Mesa and LLVM (~170 MB).
# LVA only plays audio, so these are purged without their dependents.
RUN apt-get update && \
    EXTRA_PACKAGES="" && \
    if [ "$TARGETARCH" = "arm" ]; then EXTRA_PACKAGES="libopenblas0"; fi && \
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
    procps \
    $EXTRA_PACKAGES && \
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

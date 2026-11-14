# syntax=docker/dockerfile:1

ARG INSTALL_SHERPA_KWS=0

### Build stage: compile the Python dependencies (pymicro-features needs a compiler)
FROM python:3.13-slim-trixie AS build

ARG INSTALL_SHERPA_KWS
ARG TARGETARCH

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install --yes --no-install-recommends build-essential && \
    apt-get clean

WORKDIR /app

COPY pyproject.toml setup.cfg version.txt version_githash.txt ./
COPY linux_voice_assistant/ ./linux_voice_assistant/

### Optional prebuilt sherpa-onnx wheel with RK3588 NPU support and the RKNPU2
# SDK runtime (librknnrt.so, which the NPU wheel links against), both bound
# from the wheels/ directory via RUN --mount so neither lands in an image
# layer. On arm64 builds with INSTALL_SHERPA_KWS=1 and a
# sherpa_onnx-*-cp313-*_aarch64.whl present, the wheel is installed with
# --no-index, so the plain PyPI wheel (no NPU support) can never silently win
# (see wheels/README.md), and the RKNN libraries from RKNPU2_SDK-*.tgz are
# staged into /out/rknn-libs for the runtime stage. Any other configuration
# (no flag, other arch, no wheel) installs sherpa-onnx from PyPI via the
# sherpa-kws extra and stages no RKNN libraries.
RUN --mount=type=bind,source=wheels,target=/tmp/wheels \
    python -m venv .venv && \
    .venv/bin/pip install --no-cache-dir . && \
    mkdir -p /out/rknn-libs && \
    if [ "$INSTALL_SHERPA_KWS" = "1" ]; then \
    if [ "$TARGETARCH" = "arm64" ] && \
    ls /tmp/wheels/sherpa_onnx-*-cp313-*_aarch64.whl >/dev/null 2>&1; then \
    .venv/bin/pip install --no-cache-dir --no-index --find-links /tmp/wheels sherpa-onnx && \
    SDK_TGZ=$(ls /tmp/wheels/RKNPU2_SDK-*.tgz 2>/dev/null | head -n1) && \
    { [ -n "$SDK_TGZ" ] || { echo "ERROR: RKNPU2_SDK-*.tgz not found in wheels/ (required for the NPU sherpa-onnx wheel)"; exit 1; }; } && \
    mkdir /tmp/rknn && \
    tar -zxf "$SDK_TGZ" -C /tmp/rknn && \
    tar -zxf /tmp/rknn/*/rknpu2_runtime*.tar.gz -C /tmp/rknn && \
    cp /tmp/rknn/runtime/Linux/librknn_api/aarch64/* /out/rknn-libs/; \
    else \
    .venv/bin/pip install --no-cache-dir ".[sherpa-kws]"; \
    fi; \
    fi && \
    .venv/bin/pip uninstall --yes pip setuptools wheel && \
    find .venv -name '__pycache__' -prune -exec rm -rf {} +

### Runtime stage
FROM python:3.13-slim-trixie

ENV LANG=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

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

### RK3588 NPU runtime libraries staged by the build stage; the directory is
### empty (no-op) unless INSTALL_SHERPA_KWS=1 with an NPU wheel present.
COPY --from=build /out/rknn-libs/ /usr/local/lib/
RUN ldconfig

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

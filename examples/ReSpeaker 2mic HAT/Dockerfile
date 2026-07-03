FROM python:3.13-slim-trixie

ENV LANG=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

LABEL \
    org.opencontainers.image.title="ReSpeaker 2-Mic Pi HAT Peripheral Controller" \
    org.opencontainers.image.description="LED and button controller for ReSpeaker 2-Mic Pi HAT, communicating with Linux Voice Assistant" \
    org.opencontainers.image.source="https://github.com/OHF-Voice/linux-voice-assistant"

# ----------------------------------------------------------------------------
# System packages
#
# The lgpio Python package wraps the system liblgpio C library, which is
# not in Debian's repos (Raspberry Pi OS ships it, upstream Debian does
# not). On aarch64 with no prebuilt wheel we compile the C library from
# source and then build the Python bindings against it.
#
#   build-essential   compiles the lgpio C library, spidev, and the
#                     Python lgpio extension
#   python3-dev       Python C headers for the Python lgpio extension
#   swig              generates the Python lgpio bindings at build time
#   git               clones the lgpio C library source
# ----------------------------------------------------------------------------
RUN apt-get update && \
    apt-get install --yes --no-install-recommends \
        build-essential \
        python3-dev \
        swig \
        git \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Build and install the lgpio C library so the lgpio Python package can
# link against it. Pinned to a tagged release (v0.2.2) for reproducibility.
RUN git clone --depth 1 --branch v0.2.2 https://github.com/joan2937/lg.git /tmp/lg && \
    cd /tmp/lg && make && make install && ldconfig && \
    rm -rf /tmp/lg

WORKDIR /app

# ----------------------------------------------------------------------------
# Python dependencies
#
#   websockets  – WebSocket client for LVA peripheral API
#   spidev      – low-level SPI access for APA102 LEDs
#   gpiozero     – GPIO button input (Pi 3/4/5 compatible)
#   lgpio         – gpiozero backend; works on Pi 3/4/5 including Pi 5
# ----------------------------------------------------------------------------
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY respeaker_2mic_hat.py ./

# Make /app writable by the runtime user. lgpio creates notification
# files (.lgd-nfy-N) in the process working directory at startup, and
# compose runs this container as a non root user for device access.
# The default root owned /app blocks lgpio from initialising, which
# makes gpiozero fall through every pin factory.
RUN chmod a+w /app

ENTRYPOINT ["python3", "respeaker_2mic_hat.py"]
CMD []
FROM python:3.13-slim-bookworm

ENV LANG=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

LABEL \
    org.opencontainers.image.title="Satellite 1 HAT Peripheral Controller" \
    org.opencontainers.image.description="LED ring and button controller for Satellite 1 HAT Board, communicating with Linux Voice Assistant" \
    org.opencontainers.image.source="https://github.com/OHF-Voice/linux-voice-assistant"

# ----------------------------------------------------------------------------
# System packages
#
#   build-essential   – compiles rpi-ws281x C extension
#   python3-dev       – Python C headers for native extensions
#   libgpiod-dev      – optional gpiod alternative; also satisfies some GPIO deps
#   git               – rpi-ws281x sometimes pulls from git during install
# ----------------------------------------------------------------------------
RUN apt-get update && \
    apt-get install --yes --no-install-recommends \
        build-essential \
        python3-dev \
        libgpiod-dev \
        git \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ----------------------------------------------------------------------------
# Python dependencies
#
#   websockets    – WebSocket client to talk to LVA peripheral API
#   rpi-ws281x    – SK6812 / WS2812 LED ring driver (PWM + DMA)
#   RPi.GPIO      – GPIO button input
# ----------------------------------------------------------------------------
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY Satellite1_HAT_Board.py ./

ENTRYPOINT ["python3", "Satellite1_HAT_Board.py"]
CMD []

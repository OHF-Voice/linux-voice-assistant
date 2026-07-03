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
#   git               – rpi-ws281x sometimes pulls from git during install
#
# Note: libgpiod-dev is NOT needed — gpiozero uses the lgpio backend
# (installed via pip) which works on Pi 3/4/5 without RPi.GPIO.
# ----------------------------------------------------------------------------
RUN apt-get update && \
    apt-get install --yes --no-install-recommends \
        build-essential \
        python3-dev \
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
#   gpiozero      – GPIO button input (Pi 3/4/5 compatible)
#   lgpio         – gpiozero backend; works on Pi 3/4/5 including Pi 5
# ----------------------------------------------------------------------------
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY Satellite1_HAT_Board.py ./

ENTRYPOINT ["python3", "Satellite1_HAT_Board.py"]
CMD []

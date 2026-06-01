FROM python:3.13-slim-bookworm

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
#   build-essential  – compiles spidev C extension
#   python3-dev      – Python C headers
# ----------------------------------------------------------------------------
RUN apt-get update && \
    apt-get install --yes --no-install-recommends \
        build-essential \
        python3-dev \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

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

ENTRYPOINT ["python3", "respeaker_2mic_hat.py"]
CMD []
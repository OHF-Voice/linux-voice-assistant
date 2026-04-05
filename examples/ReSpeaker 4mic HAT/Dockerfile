FROM python:3.13-slim-bookworm

ENV LANG=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

LABEL \
    org.opencontainers.image.title="ReSpeaker 4-Mic Array Peripheral Controller" \
    org.opencontainers.image.description="LED ring and button controller for ReSpeaker 4-Mic Array HAT, communicating with Linux Voice Assistant" \
    org.opencontainers.image.source="https://github.com/OHF-Voice/linux-voice-assistant"

# ----------------------------------------------------------------------------
# System packages
#
#   build-essential  – compiles spidev / apa102-pi C extensions
#   python3-dev      – Python C headers
#   git              – some pip packages pull from git during install
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
#   websockets   – WebSocket client for LVA peripheral API
#   apa102-pi    – APA102 LED ring driver (SPI)
#   spidev       – low-level SPI access (used by apa102-pi)
#   RPi.GPIO     – GPIO button input
# ----------------------------------------------------------------------------
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY respeaker_4mic_hat.py ./

ENTRYPOINT ["python3", "respeaker_4mic_hat.py"]
CMD []
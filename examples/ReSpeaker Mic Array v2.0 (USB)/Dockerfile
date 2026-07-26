FROM python:3.13-slim-bookworm

ENV LANG=C.UTF-8
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

LABEL \
    org.opencontainers.image.title="ReSpeaker Mic Array v2.0 Peripheral Controller" \
    org.opencontainers.image.description="LED controller for ReSpeaker Mic Array v2.0 (USB), communicating with Linux Voice Assistant" \
    org.opencontainers.image.source="https://github.com/OHF-Voice/linux-voice-assistant"

# ----------------------------------------------------------------------------
# System packages
#
#   libusb-1.0-0  – required by pyusb for USB device access
# ----------------------------------------------------------------------------
RUN apt-get update && \
    apt-get install --yes --no-install-recommends \
        libusb-1.0-0 \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ----------------------------------------------------------------------------
# Python dependencies
#
#   websockets  – WebSocket client for LVA peripheral API
#   pyusb       – USB vendor control transfer for LED control
# ----------------------------------------------------------------------------
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY respeaker_usb_mic_array.py ./

ENTRYPOINT ["python3", "respeaker_usb_mic_array.py"]
CMD []
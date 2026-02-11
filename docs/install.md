# Linux-Voice-Assistant - Installation

This guide describes how to install PipeWire, Docker, and then set up the Linux-Voice-Assistant using docker-compose manually. The Linux-Voice-Assistant is a voice assistant that runs locally and supports wake word detection, voice activity detection, and audio playback.

## Prerequisites:

- A Linux system (tested on Debian/Raspberry Pi OS/Ubuntu)
- Docker and Docker Compose installed (Optional but preffered)
- A microphone and speaker for audio input/output

## Overview:

The installation process can be divided into the following steps:

```
+-----------------------------------------------------------------------+
|                     LINUX-VOICE-ASSISTANT INSTALLATION                |
+-----------------------------------------------------------------------+
                                |
                                v
                +-------------------------------+
                |  0) Use Prebuilt Pi Image?    |
                |     +---------+---------+     |
                |     |   YES   |   NO    |     |
                |     +---------+---------+     |
                +-------------------------------+
                      /             \
                     /              \
                    v               v
               +-------+      +-----------------------+
               | READY |      | 1) INSTALL AUDIO SYS  |
               |       |      +-----------------------+
               +-------+              |
                                      v
                +-------------------------------------------+
                |         Audio Service Options             |
                |  +-------------+ +-------------+          |
                |  |   Existing  | |  Install    |          |
                |  | Pipe/Pulse  | |  Audio Sys  |          |
                |  +-------------+ +-------------+          |
                |         |               |                 |
                |         v               v                 |
                |    +-------+       +-----+----------+     |
                |    |READY  |       |  a) PipeWire   |     |
                |    +-------+       |  b) PulseAudio |     |
                |                    +-----+----------+     |
                |                          |                |
                +--------------------------+----------------+
                                       /                  \
                                      v                    v
                           +---------------------+  +---------------------+
                           |   Audio Ready       |  | 2) INSTALL APP      |
                           +---------------------+  +---------------------+
                                                      |
                                                      v
                                        +---------------------------+
                                        |    Application Options    |
                                        | +-----------------------+ |
                                        | | a) Docker Compose     | |
                                        | | b) Bare Metal         | |
                                        | +-----------------------+ |
                                        |           |               |
                                        +-----------+-------#-------+
                                                   |
                                                   v
                                        +---------------------------+
                                        |      Application Ready    |
                                        +---------------------------+

```

## Installation Steps:

### Option A) Use Prebuilt Pi Image:

The easyiest way to get started is to use the prebuilt image. This image comes with all necessary configurations and drivers preinstalled.

- Download ready-to-use image from [PiCompose](https://github.com/florian-asche/PiCompose)
- Change configuration in /compose/ directory if you need to.
- You may need to reboot the board like 3 times for everything to be installed. Especially for the 2MicHat drivers. And give docker-compose some time to download the images. You can watch the logs in /var/logs/picompose.log
- More information within the PiCompose project.

### Option B) Install on your own:

If you want to setup the software on your own hardware, for example if you want to use LVA on your linux desktop, you can use the docker image.

#### Step 1: Install Audioservice:

It is on your own to choose the audio service you want to use.

- **a) Existing PipeWire/PulseAudio** Use if already installed
- **b) Install PipeWire** (recommended): Install PipeWire and configure. See [Install Audioservice - Pipewire](install_audioserver.md)
- **c) Install PulseAudio** Install PulseAudio as alternative. See [Install Audioservice - PulseAudio](install_audioserver.md)

#### Step 2: Install Application:

You can run the application in two ways:

- **a) Docker Compose** (recommended): Install Docker, download compose files, configure and start. See [Install Application - Docker Compose](install_application.md)
- **b) Bare Metal** Install dependencies, clone repo, setup and create systemd service. See [Install Application - Bare Metal](install_application.md)

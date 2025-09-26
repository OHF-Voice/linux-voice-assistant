# Use a slim Python 3.13 image (works on x86_64 and aarch64 platforms)
FROM python:3.13-slim


ENV PYTHONUNBUFFERED=1
WORKDIR /app


# Install system dependencies required by the project
RUN apt-get update \
&& apt-get install -y --no-install-recommends \
libportaudio2 \
build-essential \
libmpv-dev \
ffmpeg \
pkg-config \
libasound2-dev \
&& rm -rf /var/lib/apt/lists/*


# Copy project files
COPY . /app


# Install python dependencies and the package in editable mode
RUN pip install --upgrade pip setuptools wheel \
&& pip install -e .


# Expose ESPHome-compatible port (the project uses 6053 in the README)
EXPOSE 6053


# Default entrypoint: run the package. Container should be started with --name / additional args or env variables.
ENTRYPOINT ["python3", "-m", "linux_voice_assistant"]
CMD ["--help"]

# copy entrypoint
COPY entrypoint.py /entrypoint.py
RUN chmod +x /entrypoint.py

# Use the entrypoint script (it will exec python -m linux_voice_assistant)
ENTRYPOINT ["/entrypoint.py"]

# Default args (entrypoint will add --name from env)
CMD ["--host", "0.0.0.0", "--port", "6053", "--debug"]

# The entrypoint script will handle starting the application with the correct user permissions.
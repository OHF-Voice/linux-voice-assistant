FROM ghcr.io/astral-sh/uv:python3.14-bookworm

ENV UV_COMPILE_BYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y wireplumber libhidapi-dev python3-hidapi python3-hid wireplumber

COPY . /app
WORKDIR /app

RUN uv sync --locked

CMD uv run main.py
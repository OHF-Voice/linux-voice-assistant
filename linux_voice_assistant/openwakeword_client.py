import json
import logging
import socket
import threading
from typing import Callable, Optional, List, Dict, Any
from urllib.parse import urlparse
from pathlib import Path
import time

# Models that should NOT appear in UI dropdowns
EXCLUDED_OWW_MODELS = {"embedding_model", "melspectrogram"}


# Models that are not actual wake-word detectors and should never be shown
_OWW_DETECT_MODEL_DENYLIST = {"embedding_model", "melspectrogram"}

import re

def _prettify_model_name(name: str) -> str:
    """
    Convert 'hey_jarvis_v0.1' -> 'Hey Jarvis', 'ok_nabu_v0.1' -> 'OK Nabu',
    'hal_v2' -> 'Hal', 'marvin_v2' -> 'Marvin'.
    """
    stem = Path(name).stem
    # Strip common version suffixes
    stem = re.sub(r"(?:_v\d+(?:\.\d+)?)$", "", stem)
    parts = stem.split("_")
    if parts and parts[0].lower() == "ok":
        parts[0] = "OK"
    else:
        parts = [p.capitalize() for p in parts]
    return " ".join(parts)


def _normalize_model_name(name: str) -> str:
    # Accept "foo_v2", "foo_v2.tflite", full paths, etc.
    stem = Path(name).stem
    return stem


def _filter_detect_models(models: list[str]) -> list[str]:
    # Remove helper models and de-duplicate
    normalized = {_normalize_model_name(m) for m in models}
    return sorted(m for m in normalized if m not in _OWW_DETECT_MODEL_DENYLIST)


_LOGGER = logging.getLogger(__name__)


def _jsonl(obj: dict) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")


class WyomingWakeClient:
    """Minimal client for Wyoming wake detection (openWakeWord)."""

    def __init__(self, uri: str, name: str, rate=16000, width=2, channels=1):
        self.uri, self.name = uri, name
        self.rate, self.width, self.channels = rate, width, channels
        self._paused = False
        self._suppress_until_ms = 0
        self._refractory_ms = 0
        self._sock: Optional[socket.socket] = None
        self._reader: Optional[threading.Thread] = None
        self._on_detect: Optional[Callable[[str, Optional[int]], None]] = None
        self._closed = False
        # model discovery cache
        self._models_cache: List[Dict[str, Any]] = []
        self._info_event = threading.Event()

    def connect(self, on_detect: Callable[[str, Optional[int]], None]) -> None:
        self._on_detect = on_detect
        parsed = urlparse(self.uri)
        if parsed.scheme != "tcp":
            raise ValueError(f"Only tcp:// URIs are supported (got {self.uri})")
        self._sock = socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 10400))
        self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        # Start audio stream
        self._send({"type": "audio-start", "data": {"rate": self.rate, "width": self.width, "channels": self.channels}})
        # Ask for model info
        try:
            self._send({"type": "describe"})
        except Exception:
            pass
        # Set initial detect model
        if self.name:
            try:
                self._send({"type": "detect", "data": {"names": [self.name]}})
            except Exception:
                pass
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        _LOGGER.debug("Connected to Wyoming wake server at %s", self.uri)

    def close(self) -> None:
        self._closed = True
        try:
            self._send({"type": "audio-stop"})
        except Exception:
            pass
        try:
            if self._sock:
                self._sock.close()
        finally:
            self._sock = None

    def send_audio_chunk(self, pcm: bytes, timestamp_ms: Optional[int] = None) -> None:
        if self._sock is None or self._closed:
            return
        hdr = {"type": "audio-chunk",
               "data": {"rate": self.rate, "width": self.width, "channels": self.channels},
               "payload_length": len(pcm)}
        if timestamp_ms is not None:
            hdr["data"]["timestamp"] = timestamp_ms
        self._sock.sendall(_jsonl(hdr))
        self._sock.sendall(pcm)

    
    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def suppress(self, ms: int) -> None:
        try:
            now_ms = int(time.time() * 1000)
        except Exception:
            now_ms = 0
        self._suppress_until_ms = max(self._suppress_until_ms, now_ms + int(ms))

    def set_refractory(self, ms: int) -> None:
        self._refractory_ms = max(0, int(ms))



    def list_models(self):
        """Query models using a short-lived connection with timeout; accept 'describe' or 'info'."""
        parsed = urlparse(self.uri)
        if parsed.scheme != "tcp":
            raise ValueError(f"Only tcp:// URIs are supported (got {self.uri})")
        host, port = (parsed.hostname or "127.0.0.1", parsed.port or 10400)
        s = socket.create_connection((host, port))
        try:
            s.settimeout(1.5)
            s.sendall(_jsonl({"type": "describe"}))
            f = s.makefile("rb")
            while True:
                try:
                    line = f.readline()
                except Exception:
                    break
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8"))
                except Exception:
                    continue
                mtype = msg.get("type")
                if mtype in ("describe", "info"):
                    data = msg.get("data", {}) or {}
                    wake = data.get("wake", {}) or {}
                    models = wake.get("models") or wake.get("names") or []
                    norm: List[Dict[str, Any]] = []
                    for m in models:
                        if isinstance(m, str):
                            norm.append({"name": m})
                        elif isinstance(m, dict):
                            norm.append(m)
                    out: List[Dict[str, Any]] = []
                    for m in norm:
                        name = (m.get('name') or '').strip()
                        if not name or name in EXCLUDED_OWW_MODELS:
                            continue
                        phrase = (m.get('phrase') or '').strip()
                        desc = (m.get('description') or '').strip()
                        try:
                            label = phrase or desc or _prettify_model_name(name)  # type: ignore[name-defined]
                        except NameError:
                            stem = Path(name).stem
                            stem = re.sub(r"(?:_v\d+(?:\.\d+)?)$", "", stem)
                            parts = stem.split("_")
                            if parts and parts[0].lower() == "ok":
                                parts[0] = "OK"
                            else:
                                parts = [p.capitalize() for p in parts]
                            label = " ".join(parts)
                        mm = dict(m)
                        mm['label'] = label
                        out.append(mm)
                    return out
        finally:
            try:
                s.close()
            except Exception:
                pass
        return []

    def get_models(self, timeout: float = 1.5):
        """Return cached models after waiting briefly for an 'info'/'describe' update."""
        self._info_event.wait(timeout)
        return list(self._models_cache)

    def set_detect(self, names):
        if self._sock is None:
            raise RuntimeError("Not connected")
        filtered = [n for n in list(names) if n]
        if not filtered:
            _LOGGER.warning("Ignoring empty detect() request (no model names)")
            return
        self.name = filtered[0]
        _LOGGER.debug("Requesting Wyoming detect models: %s", filtered)
        self._send({"type": "detect", "data": {"names": filtered}})


    # internals
    def _send(self, obj: dict) -> None:
        assert self._sock is not None
        self._sock.sendall(_jsonl(obj))

# --- paste this to replace the whole _read_loop method ---
    def _read_loop(self) -> None:
        assert self._sock is not None
        f = self._sock.makefile("rb")
    
        buf = ""  # text buffer for concatenated JSON objects
    
        def _yield_objects_from_text(text: str):
            """Yield JSON objects from `text` even if they are concatenated like {}{}..."""
            start = 0
            depth = 0
            in_str = False
            escape = False
            for i, ch in enumerate(text):
                if in_str:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch == "{":
                        if depth == 0:
                            start = i
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            # complete object from start..i
                            yield text[start : i + 1], i + 1
            # return here with any remainder unparsed
            return
    
        while not self._closed:
            chunk = f.readline()
            if not chunk:
                break
    
            try:
                piece = chunk.decode("utf-8")
            except Exception:
                try:
                    _LOGGER.debug("Wyoming raw (non-utf8): %r", chunk[:200])
                except Exception:
                    pass
                continue
    
            buf += piece
    
            # Try to peel off as many complete JSON objects as we can
            pos = 0
            progressed = True
            while progressed and pos < len(buf):
                progressed = False
                for obj_text, end_pos in _yield_objects_from_text(buf[pos:]):
                    progressed = True
                    pos += end_pos
                    try:
                        msg = json.loads(obj_text)
                    except Exception:
                        try:
                            _LOGGER.debug("Wyoming raw (bad-json): %r", obj_text[:200])
                        except Exception:
                            pass
                        continue
    
                    mtype = str(msg.get("type") or "").lower()
                    try:
                        _LOGGER.debug("Received Wyoming message type=%s", mtype or "<?>")
                        if mtype and mtype not in ("info", "describe"):
                            _LOGGER.debug("Wyoming raw: %r", obj_text[:200])
                    except Exception:
                        pass
    
                    # ----- model info path -----
                    if mtype in ("info", "describe"):
                        data = msg.get("data", {}) or {}
                        wake = data.get("wake", {}) if isinstance(data, dict) else {}
    
                        models = []
                        if isinstance(wake, dict):
                            if isinstance(wake.get("models"), list):
                                models = wake.get("models")
                            elif isinstance(wake.get("names"), list):
                                models = wake.get("names")
    
                        norm = []
                        for m in models:
                            if isinstance(m, str):
                                norm.append({"name": m})
                            elif isinstance(m, dict):
                                norm.append(m)
    
                        if norm:
                            self._models_cache = norm
                            self._info_event.set()
                            _LOGGER.debug(
                                "Wyoming model list updated: %s",
                                [m.get("name") for m in norm],
                            )
                        continue
    
                    # ----- detection path (accept several shapes) -----
                    data = msg.get("data", {}) or {}
                    is_detection = False
                    if mtype == "detection":
                        is_detection = True
                    elif mtype in ("event", "wake", "hotword"):
                        ev = str(data.get("event") or "").lower()
                        if ev in ("detection", "wake", "hotword"):
                            is_detection = True
    
                    if is_detection:
                        name = (
                            data.get("name")
                            or data.get("model")
                            or data.get("wakeword")
                            or self.name
                        )
                        ts = data.get("timestamp") or data.get("time") or data.get("ts")
                        _LOGGER.debug("Wake detected: %s ts=%s", name, ts)
                        if self._on_detect:
                            try:
                                now_ms = int(time.time() * 1000)
                                if self._paused or (self._suppress_until_ms and now_ms < self._suppress_until_ms):
                                    _LOGGER.debug("Detection suppressed (paused=%s until=%s)", self._paused, getattr(self, '_suppress_until_ms', 0))
                                else:
                                    now_ms = int(time.time() * 1000)
                                if self._paused or (self._suppress_until_ms and now_ms < self._suppress_until_ms):
                                    _LOGGER.debug("Detection suppressed (paused=%s until=%s)", self._paused, getattr(self, '_suppress_until_ms', 0))
                                else:
                                    self._on_detect(name, ts)
                                    if getattr(self, '_refractory_ms', 0):
                                        self.suppress(self._refractory_ms)
                                    if getattr(self, '_refractory_ms', 0):
                                        self.suppress(self._refractory_ms)
                            except Exception:
                                _LOGGER.exception("Error in detection callback")
    
            # keep any unconsumed remainder in buffer
            buf = buf[pos:]
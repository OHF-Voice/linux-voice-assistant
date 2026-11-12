"""sherpa-onnx KWS (keyword spotter) wake word engine.

Wraps ``sherpa_onnx.KeywordSpotter`` behind the duck-typed interface the LVA
audio loop expects (``wake_word`` attribute + ``process_streaming``), so
sherpa-onnx models can be selected from the Home Assistant UI like any other
wake word.

Model directory layout (one subdirectory per wake word, under a
``--wake-word-dir``):

    wakewords/sherpa-onnx/<model_id>.json

Example JSON config:

    {
        "type": "sherpa-onnx",
        "wake_word": "включи свет",
        "trained_languages": ["ru"],
        "sherpa-onnx": {
            "model_dir": "/opt/sherpa-models/kws",
            "encoder": "encoder-epoch-12-avg-2.onnx",
            "decoder": "decoder-epoch-12-avg-2.onnx",
            "joiner": "joiner-epoch-12-avg-2.onnx",
            "tokens": "tokens.txt",
            "keywords_file": "keywords.txt",
            "num_threads": 2,
            "provider": "cpu",
            "max_active_paths": 4,
            "keywords_score": 1.0
        }
    }

``model_dir`` entries may be absolute or relative to the JSON config file.
Install the runtime with:  pip install sherpa-onnx
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from .models import AUDIO_SAMPLE_RATE

_LOGGER = logging.getLogger(__name__)


class SherpaKwsWakeWord:
    """Duck-typed wake word model for sherpa-onnx KeywordSpotter.

    The audio loop only relies on:
      * ``wake_word``  - display phrase (set by the caller)
      * ``process_streaming`` - feed raw 16 kHz s16le audio bytes or float32
        numpy arrays; returns True once a keyword was spotted since the last
        reset.
    """

    def __init__(
        self,
        wake_word: str,
        wake_word_id: str,
        model_path: Path,
        model_files: Dict[str, Optional[str]],
        engine_config: Dict[str, Any],
        probability_cutoff: float = 0.7,
        supports_sensitivity: bool = False,
    ) -> None:
        self.wake_word = wake_word
        self.id = wake_word_id
        self.model_path = Path(model_path)
        self.model_files = dict(model_files or {})
        self.engine_config = dict(engine_config or {})
        self.probability_cutoff = probability_cutoff
        # Capability flag matching the other engines' duck-typed interface.
        # Always False: the spotter thresholds inside the keywords file and
        # cannot be re-thresholded at runtime via probability_cutoff.
        self.supports_sensitivity = supports_sensitivity

        self._spotter = self._load_spotter()
        self._detected = False
        self._last_keyword: Optional[str] = None
        self.is_active = False

    # ------------------------------------------------------------------

    @classmethod
    def resolve_paths(cls, config_path: Path, engine_config: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """Resolve model file entries to absolute paths.

        Mirrors how ``wake_word.py`` resolves the single ``model`` entry for
        openWakeWord (relative to the JSON config) and how pymicro-wakeword's
        ``from_config`` does it for micro: this class owns the same logic for
        sherpa-onnx, whose config may additionally set a base ``model_dir``
        (absolute, or relative to the JSON config) that all file entries
        (encoder/decoder/joiner/tokens/keywords_file) are resolved against.
        Called by ``AvailableWakeWord.load()`` so that path handling has a
        single owner per engine and ``_load_spotter`` receives ready paths.
        """
        config = engine_config or {}
        model_dir = Path(config.get("model_dir", ""))
        if not model_dir.is_absolute():
            model_dir = (Path(config_path).parent / model_dir).resolve()

        model_files: Dict[str, Optional[str]] = {}
        for name in ("encoder", "decoder", "joiner", "tokens", "keywords_file"):
            value = config.get(name)
            if not value:
                model_files[name] = None
                continue
            path = Path(value)
            if not path.is_absolute():
                path = model_dir / path
            model_files[name] = str(path)
        return model_files

    def _load_spotter(self):
        try:
            import sherpa_onnx
        except ImportError as ex:
            raise RuntimeError("sherpa-onnx is required for 'sherpa-onnx' wake words. Install it with: pip install sherpa-onnx") from ex

        config = self.engine_config
        model_files = self.model_files

        encoder = model_files["encoder"]
        decoder = model_files["decoder"]
        joiner = model_files["joiner"]
        tokens = model_files["tokens"]
        keywords_file = model_files["keywords_file"]

        missing = [
            name
            for name, value in (
                ("encoder", encoder),
                ("decoder", decoder),
                ("joiner", joiner),
                ("tokens", tokens),
            )
            if value is None
        ]
        if missing:
            raise RuntimeError(f"sherpa-onnx wake word '{self.id}': missing required config keys: {', '.join(missing)}")

        # sherpa_onnx is a compiled extension: pylint cannot introspect its members.
        # KeywordSpotter takes its options as direct keyword arguments (there is no
        # KeywordSpotterConfig in the published python API).
        spotter = sherpa_onnx.KeywordSpotter(  # pylint: disable=no-member
            tokens=tokens,
            encoder=encoder,
            decoder=decoder,
            joiner=joiner,
            keywords_file=keywords_file,
            num_threads=int(config.get("num_threads", 1)),
            provider=config.get("provider", "cpu"),
            max_active_paths=int(config.get("max_active_paths", 4)),
            keywords_score=float(config.get("keywords_score", 1.0)),
            keywords_threshold=float(config.get("keywords_threshold", 0.25)),
        )
        self._stream = spotter.create_stream()
        _LOGGER.info(
            "Loaded sherpa-onnx KWS wake word '%s' (id=%s, keywords=%s)",
            self.wake_word,
            self.id,
            keywords_file,
        )
        return spotter

    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset detection state and the underlying decoding stream."""
        self._detected = False
        self._last_keyword = None
        if self._spotter is not None:
            self._stream = self._spotter.create_stream()

    def process_streaming(self, samples: bytes) -> bool:
        """Feed raw 16 kHz s16le bytes (as produced by the LVA audio loop),
        return True if the keyword was spotted."""
        import numpy as np

        audio = np.frombuffer(samples, dtype="<i2").astype(np.float32) / 32768.0

        if audio.size == 0:
            return self._detected

        self._stream.accept_waveform(AUDIO_SAMPLE_RATE, audio)
        while self._spotter.is_ready(self._stream):
            self._spotter.decode_stream(self._stream)

        # get_result() returns "" while no keyword matched (there is no
        # stream.result object in the published python API).
        result = self._spotter.get_result(self._stream)
        if result:
            if not self._detected:
                _LOGGER.info("sherpa-onnx KWS detected keyword: %s", result)
            self._detected = True
            self._last_keyword = result

        detected = self._detected
        if detected:
            # Re-arm: after a detection the caller will start the pipeline,
            # and further audio should start a fresh search. reset_stream()
            # is the documented way to restart the search after a hit.
            self._detected = False
            self._last_keyword = None
            self._spotter.reset_stream(self._stream)

        return detected

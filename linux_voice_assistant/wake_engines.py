"""Uniform adapters over wake word engines.

Both microWakeWord and openWakeWord expose a two-stage API: a separate
feature frontend (melspectrogram / embeddings) consumes raw audio, and each
model consumes the resulting features. The frontends are expensive and carry
streaming state, so they must be SHARED between all models of the same
engine (2 wake words + stop word would otherwise triplicate the work).

These adapters hide that plumbing behind the same duck-typed interface as
``SherpaKwsWakeWord``: ```.id``, ``.wake_word``, settable ``.probability_cutoff``, a read-only
``.supports_sensitivity`` capability flag (True: the engine honours
``probability_cutoff`` at runtime; False: sherpa-onnx, which thresholds in
its keywords file) and ``process_streaming(audio_chunk) -> bool`` that
takes raw 16 kHz s16le bytes. The audio loop in ``__main__.py`` then treats
all engines identically.
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Tuple, Union

import numpy as np

if TYPE_CHECKING:
    from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures
    from pyopen_wakeword import OpenWakeWord, OpenWakeWordFeatures

_LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=2)
def _push_chunk(frontend: "Union[MicroWakeWordFeatures, OpenWakeWordFeatures]", audio_chunk: bytes) -> Tuple[np.ndarray, ...]:
    """Feed the chunk into the engine's streaming frontend exactly once.

    The audio loop feeds the SAME raw chunk to every adapter of an engine
    (wake words + stop word). The frontend carries streaming state, so the
    chunk must reach it exactly once per audio loop pass; all adapters of
    that engine then consume the cached result. lru_cache(maxsize=2) keeps
    the current chunk and evicts the previous one automatically.
    """
    return tuple(frontend.process_streaming(audio_chunk))


class MicroWakeWordAdapter:
    """microWakeWord model fed with raw audio through the shared frontend.

    The melspectrogram frontend (MicroWakeWordFeatures) carries streaming
    state and is expensive, so ONE instance is shared between all micro
    models (wake words + stop word) via the class attribute below.
    """

    _inner: "MicroWakeWord"
    _frontend: Optional["MicroWakeWordFeatures"] = None  # shared per class

    @staticmethod
    def load_inner(config_path: Path) -> "MicroWakeWord":
        from pymicro_wakeword import MicroWakeWord

        return MicroWakeWord.from_config(config_path=config_path)

    def __init__(
        self,
        inner: "MicroWakeWord",
        wake_word: str,
        id: str,  # pylint: disable=redefined-builtin
        probability_cutoff: float,
        supports_sensitivity: bool = True,
    ) -> None:
        self._inner = inner
        # Adapter-level metadata; owned here so every engine exposes the same
        # uniform interface regardless of what the inner model provides.
        self.wake_word = wake_word
        self.id = id
        self.probability_cutoff = probability_cutoff
        self.supports_sensitivity = supports_sensitivity
        self.is_active = False

    @classmethod
    def _get_frontend(cls) -> "MicroWakeWordFeatures":
        if cls._frontend is None:
            from pymicro_wakeword import MicroWakeWordFeatures

            cls._frontend = MicroWakeWordFeatures()
            _LOGGER.debug("Created shared MicroWakeWordFeatures frontend")
        return cls._frontend

    def process_streaming(self, audio_chunk: bytes) -> bool:
        inner = self._inner
        inner.debug_probabilities = False
        inner.probability_cutoff = self.probability_cutoff

        activated = False
        for features in _push_chunk(self._get_frontend(), audio_chunk):
            if inner.process_streaming(features):
                inner.debug_probabilities = True
                activated = True
        return activated

    def __getattr__(self, name: str) -> Any:
        # Delegate everything else (reset, model internals, ...) to the inner model.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)


class OpenWakeWordAdapter:
    """openWakeWord model fed with raw audio through the shared frontend.

    The embedding frontend (OpenWakeWordFeatures) carries streaming state
    and is expensive, so ONE instance is shared between all oww models
    (wake words + stop word) via the class attribute below.
    """

    _inner: "OpenWakeWord"
    _frontend: Optional["OpenWakeWordFeatures"] = None  # shared per class

    @classmethod
    def _get_frontend(cls) -> "OpenWakeWordFeatures":
        if cls._frontend is None:
            from pyopen_wakeword import OpenWakeWordFeatures

            cls._frontend = OpenWakeWordFeatures.from_builtin()
            _LOGGER.debug("Created shared OpenWakeWordFeatures frontend")
        return cls._frontend

    def __init__(
        self,
        inner: "OpenWakeWord",
        wake_word: str,
        id: str,  # pylint: disable=redefined-builtin
        probability_cutoff: float = 0.7,
        supports_sensitivity: bool = True,
    ) -> None:
        self._inner = inner
        self.wake_word = wake_word
        self.id = id
        self.probability_cutoff = probability_cutoff
        self.supports_sensitivity = supports_sensitivity
        self.is_active = False

    def process_streaming(self, audio_chunk: bytes) -> bool:
        activated = False
        for embeddings in _push_chunk(self._get_frontend(), audio_chunk):
            for prob in self._inner.process_streaming(embeddings):
                if prob > self.probability_cutoff:
                    _LOGGER.debug(
                        "Wake word '%s' activated (probability %.3f exceeded threshold %.3f)",
                        self.wake_word,
                        prob,
                        self.probability_cutoff,
                    )
                    activated = True
        return activated

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)

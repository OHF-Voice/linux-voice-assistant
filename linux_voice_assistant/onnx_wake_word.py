"""openWakeWord-compatible wake word inference backed by ONNX Runtime.

LVA's stock openWakeWord engine loads the classifier through
``pyopen_wakeword``, which is TensorFlow-Lite only.  That blocks heads that
cannot be lowered to builtin TFLite ops -- notably livekit-wakeword's
``conv_attention`` head, whose 100x false-positive-rate win only exists in the
ONNX artifact.

The shared feature front-end (mel spectrogram + Google speech embedding) is
identical for both engines, so this module reuses the *same*
``OpenWakeWordFeatures`` streaming extractor and only swaps the classifier
backend for an ONNX Runtime session.  The classifier contract is exactly the
openWakeWord one: input ``(1, input_windows, 96)`` float32, output ``(1, 1)``
sigmoid score.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Final, Optional, Union

import numpy as np

_LOGGER = logging.getLogger(__name__)

# Must match pyopen_wakeword's openWakeWord front-end constants.
WW_FEATURES: Final = 96
MAX_EMB: Final = 80  # 10 s * 8 embeddings/s


class OnnxWakeWord:
    """A wake word classifier loaded from an ONNX file via onnxruntime.

    Duck-typed to match the subset of ``pyopen_wakeword.OpenWakeWord`` that LVA
    uses: ``process_streaming(embeddings)`` yields a probability per sliding
    window, and the caller applies its own threshold.
    """

    def __init__(
        self,
        id: str,  # pylint: disable=redefined-builtin
        *,
        onnx_model: Union[str, Path],
        providers: Optional[list[str]] = None,
    ):
        import onnxruntime as ort  # imported lazily so the dep stays optional

        self.id = id
        self.onnx_model = Path(onnx_model).resolve()
        # Set by AvailableWakeWord.load() to the human-readable phrase.
        self.wake_word: Optional[str] = None

        if not self.onnx_model.exists():
            raise FileNotFoundError(f"ONNX wake word model not found: {self.onnx_model}")

        self.session = ort.InferenceSession(
            str(self.onnx_model),
            providers=providers or ["CPUExecutionProvider"],
        )

        model_inputs = self.session.get_inputs()
        if not model_inputs:
            raise ValueError(f"ONNX model has no inputs: {self.onnx_model}")

        model_input = model_inputs[0]
        self.input_name = model_input.name

        shape = list(model_input.shape)
        if len(shape) != 3:
            raise ValueError(
                f"ONNX wake word model must take (1, windows, 96), got {shape} "
                f"({self.onnx_model})"
            )

        # Window count is the only dimension we rely on; the batch dim may be
        # dynamic (None / "batch" / -1).
        windows = shape[1]
        if not isinstance(windows, int) or windows <= 0:
            raise ValueError(
                f"ONNX wake word model must use a static window dimension, got {shape} "
                f"({self.onnx_model})"
            )
        self.input_windows = windows

        if isinstance(shape[2], int) and shape[2] != WW_FEATURES:
            raise ValueError(
                f"ONNX wake word model must use {WW_FEATURES} features, got {shape[2]} "
                f"({self.onnx_model})"
            )

        _LOGGER.debug(
            "Loaded ONNX wake word '%s' from %s (input=%s shape=%s windows=%d)",
            id,
            self.onnx_model,
            self.input_name,
            shape,
            self.input_windows,
        )

        self.new_embeddings: int = 0
        self.embeddings: np.ndarray = np.zeros(
            shape=(MAX_EMB, WW_FEATURES), dtype=np.float32
        )

    def process_streaming(self, embeddings: np.ndarray) -> Iterable[float]:
        """Yield a probability per sliding window, mirroring OpenWakeWord.

        ``embeddings`` is one chunk from ``OpenWakeWordFeatures.process_streaming``
        with shape ``(1, 1, num_frames, 96)``.
        """
        num_embedding_windows = embeddings.shape[2]

        # Shift
        self.embeddings[:-num_embedding_windows] = self.embeddings[
            num_embedding_windows:
        ]

        # Overwrite
        self.embeddings[-num_embedding_windows:] = embeddings[0, 0, :, :]
        self.new_embeddings = min(
            len(self.embeddings),
            self.new_embeddings + num_embedding_windows,
        )

        while self.new_embeddings >= self.input_windows:
            emb_tensor = np.zeros(
                shape=(1, self.input_windows, WW_FEATURES),
                dtype=np.float32,
            )
            emb_tensor[0, :] = self.embeddings[
                -self.new_embeddings : len(self.embeddings)
                - self.new_embeddings
                + self.input_windows
            ]
            self.new_embeddings = max(0, self.new_embeddings - 1)

            outputs = self.session.run([self.session.get_outputs()[0].name], {self.input_name: emb_tensor})
            yield float(np.asarray(outputs[0]).reshape(-1)[0])

    def reset(self) -> None:
        self.new_embeddings = 0
        self.embeddings = np.zeros(shape=(MAX_EMB, WW_FEATURES), dtype=np.float32)

    def close(self) -> None:
        session = getattr(self, "session", None)
        if session is not None:
            self.session = None

    def __del__(self) -> None:
        self.close()

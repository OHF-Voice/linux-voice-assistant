"""Unit tests for the ONNX Runtime wake word backend."""

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

import onnx  # noqa: E402  pylint: disable=wrong-import-position
from onnx import TensorProto, helper  # noqa: E402  pylint: disable=wrong-import-position

from linux_voice_assistant.onnx_wake_word import WW_FEATURES, OnnxWakeWord  # noqa: E402  pylint: disable=wrong-import-position


def _write_model(path: Path, windows: int = 16, features: int = WW_FEATURES) -> None:
    """Write a minimal (1, windows, features) -> (1, 1) ONNX classifier."""
    emb = helper.make_tensor_value_info("embeddings", TensorProto.FLOAT, [1, windows, features])
    score = helper.make_tensor_value_info("score", TensorProto.FLOAT, [1, 1])
    axes = helper.make_tensor("axes", TensorProto.INT64, [2], [1, 2])
    mean = helper.make_node("ReduceMean", ["embeddings", "axes"], ["pooled"], keepdims=1)
    sig = helper.make_node("Sigmoid", ["pooled"], ["score"])
    graph = helper.make_graph([mean, sig], "test_ww", [emb], [score], initializer=[axes])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    model.ir_version = 10
    onnx.save(model, path)


def test_windows_embeddings_and_emits_one_prob_per_sliding_window(tmp_path: Path) -> None:
    model_path = tmp_path / "test.tflite.onnx"
    _write_model(model_path)

    model = OnnxWakeWord(id="test", onnx_model=model_path)
    assert model.input_windows == 16
    assert model.input_name == "embeddings"

    rng = np.random.default_rng(0)

    # 8 frames is below the 16-frame window: nothing yet.
    assert list(model.process_streaming(rng.random((1, 1, 8, WW_FEATURES), dtype=np.float32))) == []

    # The next 8 frames reach the window: one probability.
    probs = list(model.process_streaming(rng.random((1, 1, 8, WW_FEATURES), dtype=np.float32)))
    assert len(probs) == 1
    assert 0.0 <= probs[0] <= 1.0

    # Streaming further slides the window one probability per frame.
    probs = list(model.process_streaming(rng.random((1, 1, 4, WW_FEATURES), dtype=np.float32)))
    assert len(probs) == 4


def test_reset_clears_windowing(tmp_path: Path) -> None:
    model_path = tmp_path / "test.onnx"
    _write_model(model_path)

    model = OnnxWakeWord(id="test", onnx_model=model_path)
    rng = np.random.default_rng(1)
    list(model.process_streaming(rng.random((1, 1, 20, WW_FEATURES), dtype=np.float32)))

    model.reset()
    assert model.new_embeddings == 0
    assert not model.embeddings.any()


def test_rejects_wrong_input_shape(tmp_path: Path) -> None:
    model_path = tmp_path / "bad.onnx"
    _write_model(model_path, windows=16, features=64)

    with pytest.raises(ValueError):
        OnnxWakeWord(id="bad", onnx_model=model_path)

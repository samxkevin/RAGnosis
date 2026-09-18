"""CohereGenerator primary/fallback and response-handling tests (no network)."""

import pytest

from multimodal.config import MultimodalConfig
from multimodal.errors import NotConfiguredError
from multimodal.service import CohereGenerator


class _Resp:
    def __init__(self, text):
        self.text = text


class _FakeCohereClient:
    """Records calls and returns/raises per-model behaviour."""

    def __init__(self, behaviour):
        # behaviour: dict model -> ("text", value) | ("raise", exc)
        self.behaviour = behaviour
        self.calls = []

    def chat(self, *, model, message, temperature):
        self.calls.append(model)
        kind, value = self.behaviour[model]
        if kind == "raise":
            raise value
        return _Resp(value)


def _generator(behaviour, config=None):
    gen = CohereGenerator(
        config
        or MultimodalConfig(
            cohere_api_key="k",
            cohere_model="primary-model",
            cohere_fallback_model="fallback-model",
        )
    )
    gen._client = _FakeCohereClient(behaviour)  # inject
    return gen


def test_not_configured_raises():
    gen = CohereGenerator(MultimodalConfig(cohere_api_key=""))
    with pytest.raises(NotConfiguredError):
        gen.generate("prompt")


def test_primary_success():
    gen = _generator({"primary-model": ("text", "a good answer")})
    text, model = gen.generate("prompt")
    assert text == "a good answer"
    assert model == "primary-model"


def test_empty_primary_response_raises():
    gen = _generator({"primary-model": ("text", "   ")})
    with pytest.raises(RuntimeError, match="empty"):
        gen.generate("prompt")


def test_fallback_on_model_error():
    gen = _generator(
        {
            "primary-model": ("raise", Exception("model not found: decommissioned")),
            "fallback-model": ("text", "fallback answer"),
        }
    )
    text, model = gen.generate("prompt")
    assert text == "fallback answer"
    assert model == "fallback-model"
    assert gen._client.calls == ["primary-model", "fallback-model"]


def test_non_model_error_does_not_trigger_fallback():
    # A generic network/timeout error is not a model-availability problem, so we
    # must NOT silently retry on the fallback model (avoids masking real errors).
    gen = _generator(
        {"primary-model": ("raise", Exception("connection timed out"))}
    )
    with pytest.raises(Exception, match="timed out"):
        gen.generate("prompt")
    assert gen._client.calls == ["primary-model"]


def test_fallback_also_fails_propagates():
    gen = _generator(
        {
            "primary-model": ("raise", Exception("unknown model")),
            "fallback-model": ("raise", Exception("unknown model too")),
        }
    )
    with pytest.raises(Exception, match="unknown model too"):
        gen.generate("prompt")


def test_no_infinite_retry():
    # Each model is attempted at most once; total attempts bounded by 2.
    gen = _generator(
        {
            "primary-model": ("raise", Exception("model decommissioned")),
            "fallback-model": ("text", "ok"),
        }
    )
    gen.generate("prompt")
    assert len(gen._client.calls) == 2

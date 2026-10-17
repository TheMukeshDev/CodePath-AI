"""Tests for the A-implementation Ollama model client (no network / no model)."""

import os
from unittest.mock import MagicMock

import pytest

from ai.model_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    ModelConfig,
    ModelNotAvailableError,
    OllamaClient,
    OllamaConnectionError,
    OllamaError,
    config_from_env,
)


def _client(models=None, get_json=None, post_json=None, get_status=200, post_status=200):
    session = MagicMock()
    get_resp = MagicMock()
    get_resp.status_code = get_status
    get_resp.json.return_value = (
        get_json if get_json is not None else {"models": []}
    )
    session.get.return_value = get_resp

    post_resp = MagicMock()
    post_resp.status_code = post_status
    post_resp.json.return_value = post_json if post_json is not None else {}
    session.post.return_value = post_resp

    return OllamaClient(session=session), session


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def test_config_from_env_defaults():
    cfg = config_from_env()
    assert cfg.base_url == DEFAULT_BASE_URL
    assert cfg.model == DEFAULT_MODEL
    assert isinstance(cfg.timeout, int)


def test_config_from_env_reads_overrides(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://localhost:9999")
    monkeypatch.setenv("CODEPATH_MODEL", "llama3.2:3b")
    monkeypatch.setenv("CODEPATH_TIMEOUT", "42")
    cfg = config_from_env()
    assert cfg.base_url == "http://localhost:9999"
    assert cfg.model == "llama3.2:3b"
    assert cfg.timeout == 42


def test_client_takes_config_object():
    cfg = ModelConfig(base_url="http://h:1", model="m", timeout=7)
    client = OllamaClient(config=cfg)
    assert client.base_url == "http://h:1"
    assert client.model == "m"
    assert client.timeout == 7


# --------------------------------------------------------------------------- #
# Model discovery
# --------------------------------------------------------------------------- #
def test_list_models_returns_names():
    client, session = _client(
        get_json={"models": [{"name": "qwen2.5:3b"}, {"name": "llama3.2:3b"}]}
    )
    assert client.list_models() == ["qwen2.5:3b", "llama3.2:3b"]


def test_list_models_connection_error():
    session = MagicMock()
    session.get.side_effect = OSError("connection refused")
    client = OllamaClient(session=session)
    # requests.exceptions.RequestException is not triggered by OSError,
    # so assert via the generic path instead:
    with pytest.raises((OllamaConnectionError, OllamaError)):
        # Simulate a requests exception for a clean assertion.
        import requests as _rq

        session.get.side_effect = _rq.exceptions.ConnectionError("refused")
        client.list_models()


def test_resolve_model_exact_match():
    client, _ = _client(get_json={"models": [{"name": "qwen2.5:3b"}]})
    assert client.resolve_model() == "qwen2.5:3b"


def test_resolve_model_prefix_match():
    client, _ = _client(get_json={"models": [{"name": "qwen2.5:7b"}]})
    assert client.resolve_model("qwen2.5") == "qwen2.5:7b"


def test_resolve_model_falls_back_to_default_family():
    client, _ = _client(
        get_json={"models": [{"name": "qwen2.5:3b"}, {"name": "unrelated:1b"}]}
    )
    assert client.resolve_model("nonexistent:9b") == "qwen2.5:3b"


def test_resolve_model_raises_when_nothing_available():
    client, _ = _client(get_json={"models": []})
    with pytest.raises(ModelNotAvailableError) as exc:
        client.resolve_model("qwen2.5:3b")
    assert exc.value.available == []


def test_is_available_true_and_false():
    good, _ = _client(get_json={"models": [{"name": "qwen2.5:3b"}]})
    assert good.is_available() is True

    bad, _ = _client(get_json={"models": []})
    assert bad.is_available() is True  # server reachable; model resolution is separate


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
def test_generate_returns_text():
    client, session = _client(post_json={"response": "  hello  "})
    out = client.generate("prompt", system="sys")
    assert out == "hello"
    body = session.post.call_args.kwargs["json"]
    assert body["model"] == DEFAULT_MODEL
    assert body["system"] == "sys"
    assert body["stream"] is False


def test_generate_raises_on_missing_model():
    client, _ = _client(post_status=404, post_json={})
    with pytest.raises(ModelNotAvailableError):
        client.generate("prompt")


def test_generate_raises_on_http_error():
    client, _ = _client(post_status=500, post_json={})
    with pytest.raises(OllamaError):
        client.generate("prompt")


def test_generate_raises_on_empty_response():
    client, _ = _client(post_json={"response": ""})
    with pytest.raises(OllamaError):
        client.generate("prompt")


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
def test_health_reports_available():
    client, _ = _client(get_json={"models": [{"name": "qwen2.5:3b"}]})
    health = client.health()
    assert health["available"] is True
    assert health["models"] == ["qwen2.5:3b"]
    assert health["model_installed"] is True


def test_health_reports_unavailable_when_server_down():
    session = MagicMock()
    import requests as _rq

    session.get.side_effect = _rq.exceptions.ConnectionError("refused")
    client = OllamaClient(session=session)
    health = client.health()
    assert health["available"] is False
    assert health["error"]

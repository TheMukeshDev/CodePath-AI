"""Local Ollama model client for CodePath AI.

The client talks to a locally running Ollama server over HTTP. It is designed
to degrade gracefully: if Ollama is not installed, not running, or does not
have the configured model, callers receive a clear, typed error rather than a
stack trace.

Configuration is intentionally minimal and never logs tokens or environment
variables. The model name is configurable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen2.5:3b"
DEFAULT_TIMEOUT = 120


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class OllamaError(Exception):
    """Base class for Ollama client errors."""


class OllamaConnectionError(OllamaError):
    """Raised when Ollama cannot be reached."""


class ModelNotAvailableError(OllamaError):
    """Raised when the requested model is not installed locally."""

    def __init__(self, message: str, available: Optional[List[str]] = None) -> None:
        super().__init__(message)
        self.available = available or []


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass
class ModelConfig:
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: int = DEFAULT_TIMEOUT
    temperature: float = 0.1
    num_ctx: int = 4096


def config_from_env() -> ModelConfig:
    """Build a :class:`ModelConfig`, honouring a couple of environment vars.

    Only non-secret settings are read. No tokens are involved for a local
    Ollama server.
    """
    return ModelConfig(
        base_url=os.environ.get("OLLAMA_HOST", DEFAULT_BASE_URL),
        model=os.environ.get("CODEPATH_MODEL", DEFAULT_MODEL),
        timeout=int(os.environ.get("CODEPATH_TIMEOUT", DEFAULT_TIMEOUT)),
    )


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class OllamaClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT,
        session: Optional[requests.Session] = None,
        config: Optional[ModelConfig] = None,
    ) -> None:
        if config is not None:
            base_url = config.base_url
            model = config.model
            timeout = config.timeout
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.session = session or requests.Session()

    # -- discovery ---------------------------------------------------------- #
    def is_available(self) -> bool:
        try:
            self.list_models()
            return True
        except OllamaError:
            return False

    def list_models(self) -> List[str]:
        """Return the names of locally installed models."""
        try:
            response = self.session.get(f"{self.base_url}/api/tags", timeout=10)
        except requests.exceptions.RequestException as exc:
            raise OllamaConnectionError(
                f"Could not reach Ollama at {self.base_url}. Is it running?"
            ) from exc
        if response.status_code >= 400:
            raise OllamaError(
                f"Ollama returned HTTP {response.status_code} for /api/tags."
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaError("Ollama returned a non-JSON response.") from exc
        models = []
        for item in data.get("models", []):
            name = item.get("name") or item.get("model")
            if name:
                models.append(name)
        return models

    def resolve_model(self, preferred: Optional[str] = None) -> str:
        """Return an installed model name, preferring *preferred* or the default.

        Raises :class:`ModelNotAvailableError` when no suitable model exists.
        """
        available = self.list_models()
        wanted = preferred or self.model

        if wanted in available:
            return wanted

        # Accept prefix matches such as ``qwen2.5`` matching ``qwen2.5:3b``.
        wanted_root = wanted.split(":")[0]
        for name in available:
            if name == wanted or name.split(":")[0] == wanted_root:
                return name

        # Sensible fallbacks that keep memory usage small (8 GB target).
        for candidate in ("qwen2.5:3b", "qwen2.5", "llama3.2", "mistral"):
            for name in available:
                if name == candidate or name.split(":")[0] == candidate.split(":")[0]:
                    return name

        raise ModelNotAvailableError(
            f"Model '{wanted}' is not installed in Ollama.", available=available
        )

    # -- generation --------------------------------------------------------- #
    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> str:
        """Generate a completion. Raises a typed :class:`OllamaError` on failure."""
        payload: Dict[str, Any] = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "num_ctx": 4096},
        }
        if system:
            payload["system"] = system

        try:
            response = self.session.post(
                f"{self.base_url}/api/generate",
                json=payload,
                timeout=timeout or self.timeout,
            )
        except requests.exceptions.Timeout as exc:
            raise OllamaConnectionError(
                "Ollama request timed out. The model may be too large for this machine."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise OllamaConnectionError(
                f"Could not reach Ollama at {self.base_url}. Is it running?"
            ) from exc

        if response.status_code == 404:
            raise ModelNotAvailableError(
                f"Model '{payload['model']}' was not found in Ollama."
            )
        if response.status_code >= 400:
            raise OllamaError(f"Ollama returned HTTP {response.status_code}.")

        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaError("Ollama returned a non-JSON response.") from exc

        text = (data.get("response") or "").strip()
        if not text:
            raise OllamaError("Ollama returned an empty response.")
        return text

    def health(self) -> Dict[str, Any]:
        """Return a small status dict suitable for display, never raising."""
        try:
            models = self.list_models()
        except OllamaError as exc:
            return {"available": False, "error": str(exc), "models": []}
        return {
            "available": True,
            "error": None,
            "models": models,
            "configured_model": self.model,
            "model_installed": self._model_installed(models),
        }

    def _model_installed(self, models: Optional[List[str]] = None) -> bool:
        available = models if models is not None else self.list_models()
        wanted_root = self.model.split(":")[0]
        return any(
            name == self.model or name.split(":")[0] == wanted_root
            for name in available
        )

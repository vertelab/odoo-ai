# -*- coding: utf-8 -*-
# Copyright (C) 2026 Vertel Sverige AB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Provider Registry — maps provider_type → implementation class.

Each implementation is a plain Python class (NOT an Odoo model).
This keeps them testable without Odoo and avoids AbstractModel complexity.

To add a provider:
1. Create a new file in this directory
2. Subclass BaseProviderImpl
3. Register it below
"""

from typing import Dict, Type, Any, List


class BaseProviderImpl:
    """Interface for provider implementations.

    Subclass this and override the methods for each provider.
    """

    label: str = "Unknown"  # Human-readable label

    def get_llm(self, provider_record, model: str, temperature: float = 0.7):
        """Return a LangChain-compatible chat model instance."""
        raise NotImplementedError()

    def get_embedding(self, provider_record, model: str):
        """Return embeddings model, or None if not supported."""
        return None

    def discover_models(self, provider_record) -> List[dict]:
        """Fetch available models from API. Return [{name, context_window, ...}]."""
        return self.get_known_models(provider_record)

    def check_health(self, provider_record):
        """Ping the provider. Raises on failure."""
        raise NotImplementedError()

    def get_known_models(self, provider_record) -> List[dict]:
        """Fallback: known models when API discovery isn't available."""
        return []

    def get_model_capabilities(self, provider_record, model_name: str) -> dict:
        """Return capabilities dict for a specific model."""
        return {
            "context_window": None,
            "supports_vision": False,
            "supports_tools": False,
            "supports_streaming": True,
        }


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

class OpenAIProviderImpl(BaseProviderImpl):
    """OpenAI + OpenAI-compatible APIs (BergetAI, Bifrost, Azure, etc.)."""

    label = "OpenAI / OpenAI-compatible"

    def get_llm(self, provider_record, model, temperature=0.7):
        from langchain_openai import ChatOpenAI
        from pydantic import SecretStr

        kwargs = {"model": model, "temperature": temperature}

        api_key = provider_record._get_api_key()
        if api_key:
            kwargs["api_key"] = SecretStr(api_key)

        base_url = provider_record._get_base_url("https://api.openai.com/v1")
        if base_url:
            kwargs["base_url"] = base_url

        return ChatOpenAI(**kwargs)

    def get_embedding(self, provider_record, model):
        from langchain_openai import OpenAIEmbeddings
        from pydantic import SecretStr

        kwargs = {"model": model}
        api_key = provider_record._get_api_key()
        if api_key:
            kwargs["api_key"] = SecretStr(api_key)
        base_url = provider_record._get_base_url("https://api.openai.com/v1")
        if base_url:
            kwargs["base_url"] = base_url
        return OpenAIEmbeddings(**kwargs)

    def discover_models(self, provider_record):
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=provider_record._get_api_key(),
                base_url=provider_record._get_base_url("https://api.openai.com/v1"),
            )
            models = client.models.list()
            return [
                {"name": m.id}
                for m in models.data
            ]
        except Exception:
            return self.get_known_models(provider_record)

    def check_health(self, provider_record):
        from openai import OpenAI

        client = OpenAI(
            api_key=provider_record._get_api_key(),
            base_url=provider_record._get_base_url("https://api.openai.com/v1"),
            max_retries=1,
            timeout=10,
        )
        client.models.list()

    def get_known_models(self, provider_record):
        base_url = provider_record._get_base_url("")

        # Berget AI — our Swedish provider
        if "berget.ai" in (base_url or ""):
            return [
                {"name": "agentica-org/DeepCoder-14B-Preview", "context_window": 200000},
                {"name": "google/gemma-3-27b-it", "context_window": 200000},
                {"name": "meta-llama/Llama-3.1-8B-Instruct", "context_window": 200000},
                {"name": "meta-llama/Llama-3.3-70B-Instruct", "context_window": 200000},
                {"name": "unsloth/DeepSeek-R1-GGUF", "context_window": 200000},
                {"name": "mistralai/Mistral-Small-3.1-24B-Instruct-2503", "context_window": 200000},
                {"name": "intfloat/multilingual-e5-large-instruct", "supports_embedding": True},
                {"name": "KBLab/kb-whisper-large", "supports_asr": True},
            ]

        # Bifrost — our AI gateway
        if "bifrost" in (base_url or "").lower():
            return [
                {"name": "claude-sonnet-4-20250514", "context_window": 200000, "supports_vision": True},
                {"name": "gpt-4o", "context_window": 128000, "supports_vision": True},
            ]

        # Default OpenAI
        return [
            {"name": "gpt-4o", "context_window": 128000, "supports_vision": True},
            {"name": "gpt-4o-mini", "context_window": 128000, "supports_vision": True},
            {"name": "gpt-4-turbo", "context_window": 128000, "supports_vision": True},
            {"name": "gpt-3.5-turbo", "context_window": 16385},
            {"name": "o3-mini", "context_window": 200000},
        ]


class AnthropicProviderImpl(BaseProviderImpl):
    """Anthropic direct API."""

    label = "Anthropic"

    def get_llm(self, provider_record, model, temperature=0.7):
        from langchain_anthropic import ChatAnthropic
        from pydantic import SecretStr

        kwargs = {"model": model, "temperature": temperature}

        api_key = provider_record._get_api_key()
        if api_key:
            kwargs["api_key"] = SecretStr(api_key)
        base_url = provider_record._get_base_url("")
        if base_url:
            kwargs["base_url"] = base_url

        return ChatAnthropic(**kwargs)

    def check_health(self, provider_record):
        from anthropic import Anthropic

        client = Anthropic(api_key=provider_record._get_api_key(), max_retries=1, timeout=10)
        client.messages.create(
            model="claude-haiku-3-5-20241022",
            max_tokens=1,
            messages=[{"role": "user", "content": "ping"}],
        )

    def get_known_models(self, provider_record):
        return [
            {"name": "claude-sonnet-4-20250514", "context_window": 200000, "supports_vision": True},
            {"name": "claude-opus-4-20250514", "context_window": 200000, "supports_vision": True},
            {"name": "claude-haiku-3-5-20241022", "context_window": 200000},
        ]


class OllamaProviderImpl(BaseProviderImpl):
    """Ollama local models."""

    label = "Ollama (Local)"

    def get_llm(self, provider_record, model, temperature=0.7):
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model,
            temperature=temperature,
            disable_streaming=True,
            base_url=provider_record._get_base_url("http://localhost:11434"),
        )

    def get_embedding(self, provider_record, model):
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            model=model,
            base_url=provider_record._get_base_url("http://localhost:11434"),
        )

    def discover_models(self, provider_record):
        import requests

        try:
            base_url = provider_record._get_base_url("http://localhost:11434")
            resp = requests.get(f"{base_url}/api/tags", timeout=10)
            resp.raise_for_status()

            result = []
            for m in resp.json().get("models", []):
                entry = {"name": m["name"]}
                try:
                    info = requests.post(
                        f"{base_url}/api/show",
                        json={"name": m["name"]},
                        timeout=10,
                    ).json()
                    model_info = info.get("model_info", {})
                    if model_info:
                        first_key = next(iter(model_info), None)
                        if first_key:
                            entry["context_window"] = model_info[first_key].get("context_length")
                except Exception:
                    pass
                result.append(entry)
            return result
        except Exception:
            return []

    def check_health(self, provider_record):
        import requests

        base_url = provider_record._get_base_url("http://localhost:11434")
        resp = requests.get(f"{base_url}/api/tags", timeout=10)
        resp.raise_for_status()


class MistralProviderImpl(BaseProviderImpl):
    """Mistral AI direct API."""

    label = "Mistral AI"

    def get_llm(self, provider_record, model, temperature=0.7):
        from langchain_mistralai import ChatMistralAI
        from pydantic import SecretStr

        kwargs = {"model": model, "temperature": temperature}
        api_key = provider_record._get_api_key()
        if api_key:
            kwargs["api_key"] = SecretStr(api_key)
        base_url = provider_record._get_base_url("")
        if base_url:
            kwargs["endpoint"] = base_url
        return ChatMistralAI(**kwargs)

    def get_embedding(self, provider_record, model):
        from langchain_mistralai import MistralAIEmbeddings
        from pydantic import SecretStr

        kwargs = {"model": model}
        api_key = provider_record._get_api_key()
        if api_key:
            kwargs["api_key"] = SecretStr(api_key)
        return MistralAIEmbeddings(**kwargs)

    def check_health(self, provider_record):
        from mistralai import Mistral

        client = Mistral(api_key=provider_record._get_api_key(), timeout_ms=10000)
        client.models.list()

    def get_known_models(self, provider_record):
        return [
            {"name": "mistral-large-latest", "context_window": 128000},
            {"name": "mistral-small-latest", "context_window": 32000},
            {"name": "codestral-latest", "context_window": 256000},
        ]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

PROVIDER_REGISTRY: Dict[str, BaseProviderImpl] = {
    "openai": OpenAIProviderImpl(),
    "anthropic": AnthropicProviderImpl(),
    "ollama": OllamaProviderImpl(),
    "mistral": MistralProviderImpl(),
    "berget": OpenAIProviderImpl(),   # BergetAI is OpenAI-compatible
    "bifrost": OpenAIProviderImpl(),  # Bifrost is OpenAI-compatible
}

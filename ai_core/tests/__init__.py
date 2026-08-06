# -*- coding: utf-8 -*-
# Copyright (C) 2026 Vertel Sverige AB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Tests for ai_core — LLM Provider Abstraction.

Run: python3 -m unittest discover -s ai_core/tests
Or inside Odoo: --test-enable --stop-after-init -d ainew -i ai_core
"""

from odoo.tests.common import TransactionCase


class TestProviderModel(TransactionCase):
    """Test the llm.provider model and its data."""

    def test_providers_exist(self):
        """All demo providers should be created."""
        providers = self.env["llm.provider"].search([])
        provider_types = providers.mapped("provider_type")

        self.assertIn("openai", provider_types)
        self.assertIn("anthropic", provider_types)
        self.assertIn("ollama", provider_types)
        self.assertIn("mistral", provider_types)
        self.assertIn("berget", provider_types)
        self.assertIn("bifrost", provider_types)

    def test_provider_api_key_resolution(self):
        """API key resolution: field → odoo.conf → env var."""
        provider = self.env.ref("ai_core.provider_openai")

        # No key set anywhere → should be falsy
        self.assertFalse(provider._get_api_key())

        # Key set on field
        provider.api_key = "sk-test-key"
        self.assertEqual(provider._get_api_key(), "sk-test-key")
        provider.api_key = False

    def test_provider_base_url(self):
        """Base URL resolution: field or default."""
        provider = self.env.ref("ai_core.provider_berget")
        self.assertEqual(
            provider._get_base_url(""),
            "https://api.berget.ai/v1/",
        )

        provider = self.env.ref("ai_core.provider_openai")
        self.assertEqual(
            provider._get_base_url("https://api.openai.com/v1"),
            "https://api.openai.com/v1",
        )

    def test_provider_type_selection(self):
        """Provider type selection is dynamic from registry."""
        provider = self.env["llm.provider"].new({"name": "Test"})
        # _get_provider_types should return list from registry
        types = provider._get_provider_types()
        self.assertTrue(len(types) >= 6)
        type_keys = [t[0] for t in types]
        self.assertIn("openai", type_keys)
        self.assertIn("berget", type_keys)
        self.assertIn("bifrost", type_keys)


class TestModels(TransactionCase):
    """Test llm.model records."""

    def test_models_exist(self):
        models = self.env["llm.model"].search([])
        self.assertTrue(len(models) > 10)

    def test_berget_models(self):
        """BergetAI models from demo data."""
        berget_models = self.env["llm.model"].search([
            ("provider_type", "=", "berget"),
        ])
        model_names = berget_models.mapped("name")
        self.assertIn("meta-llama/Llama-3.3-70B-Instruct", model_names)
        self.assertIn("intfloat/multilingual-e5-large-instruct", model_names)
        self.assertIn("KBLab/kb-whisper-large", model_names)

    def test_model_context_window_display(self):
        model = self.env.ref("ai_core.model_claude_sonnet_4")
        self.assertEqual(model.context_window_display, "200K")

        model.context_window = 500
        self.assertEqual(model.context_window_display, "500")

        model.context_window = 0
        self.assertEqual(model.context_window_display, "0")


class TestSessionLine(TransactionCase):
    """Test token tracking."""

    def test_create_session_line(self):
        line = self.env["llm.session.line"].create({
            "provider_type": "openai",
            "model_name": "gpt-4o",
            "input_tokens": 1500,
            "output_tokens": 300,
        })
        self.assertEqual(line.total_tokens, 1800)

    def test_token_calculation(self):
        line = self.env["llm.session.line"].new({
            "input_tokens": 0,
            "output_tokens": 0,
        })
        self.assertEqual(line.total_tokens, 0)

        line.input_tokens = 1000
        line.output_tokens = 500
        line._compute_total_tokens()
        self.assertEqual(line.total_tokens, 1500)


class TestProviderImplementations(TransactionCase):
    """Test that provider implementations are properly registered."""

    def test_provider_registry(self):
        """All provider types have implementations."""
        from ..providers import PROVIDER_REGISTRY

        for provider_type, impl in PROVIDER_REGISTRY.items():
            self.assertTrue(hasattr(impl, "get_llm"), f"{provider_type}: missing get_llm")
            self.assertTrue(hasattr(impl, "check_health"), f"{provider_type}: missing check_health")
            self.assertTrue(hasattr(impl, "get_known_models"), f"{provider_type}: missing get_known_models")

    def test_known_models_not_empty(self):
        """Each provider has fallback known models."""
        from ..providers import PROVIDER_REGISTRY

        for provider_type, impl in PROVIDER_REGISTRY.items():
            models = impl.get_known_models(None)  # None because we don't need the record
            self.assertTrue(len(models) > 0, f"{provider_type}: known models list is empty")

    def test_berget_shared_openai_impl(self):
        """BergetAI and Bifrost share OpenAI implementation."""
        from ..providers import PROVIDER_REGISTRY

        self.assertIs(
            PROVIDER_REGISTRY["berget"].__class__,
            PROVIDER_REGISTRY["openai"].__class__,
            "BergetAI should use the same class as OpenAI",
        )
        self.assertIs(
            PROVIDER_REGISTRY["bifrost"].__class__,
            PROVIDER_REGISTRY["openai"].__class__,
            "Bifrost should use the same class as OpenAI",
        )

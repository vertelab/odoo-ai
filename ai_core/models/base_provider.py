# -*- coding: utf-8 -*-
# Copyright (C) 2026 Vertel Sverige AB
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Base LLM Provider — registry-driven delegation to typed implementations.

Each provider type has a corresponding implementation class.
The registry maps provider_type → implementation.

To add a new provider:
1. Create ai_core/providers/provider_<name>.py
2. Implement the provider class
3. Register it in the PROVIDER_REGISTRY below
"""

import logging
import os
from typing import Dict, Type, Any

from odoo import models, fields, tools
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider interface (duck-typed — NOT an Odoo model)
# Each implementation class must have these methods:
#
#   get_llm(provider_record, model, temperature) -> LangChain LLM
#   get_embedding(provider_record, model) -> LangChain Embeddings | None
#   discover_models(provider_record) -> list[dict]
#   check_health(provider_record) -> None (raises on failure)
#   get_known_models(provider_record) -> list[dict]
# ---------------------------------------------------------------------------


class BaseLLMProvider(models.Model):
    """A configured LLM provider record.

    The actual provider-specific logic lives in registry classes,
    not in Odoo model inheritance. This keeps provider implementations
    as plain Python classes — testable without Odoo.
    """

    _name = "llm.provider"
    _description = "LLM Provider"

    # --- Fields ---
    name = fields.Char(required=True)
    provider_type = fields.Selection(
        selection="_get_provider_types",
        required=True,
    )
    provider_label = fields.Char(help="Human-readable label")
    api_key = fields.Char(
        help="Leave empty to read from odoo.conf key "
        "'llm_{provider_type}_api_key' or env var "
        "'LLM_{PROVIDER_TYPE}_API_KEY'.",
    )
    base_url = fields.Char(help="Override default API endpoint.")
    is_enabled = fields.Boolean(default=True)
    last_health_check = fields.Datetime(readonly=True)
    last_health_status = fields.Selection(
        [("ok", "OK"), ("error", "Error"), ("unknown", "Unknown")],
        default="unknown",
        readonly=True,
    )
    health_check_message = fields.Text(readonly=True)

    # ------------------------------------------------------------------
    # Provider type selection — dynamic from registry
    # ------------------------------------------------------------------

    @tools.ormcache()
    def _get_provider_types(self):
        from ..providers import PROVIDER_REGISTRY

        return [(key, impl.label) for key, impl in PROVIDER_REGISTRY.items()]

    # ------------------------------------------------------------------
    # API-key resolution
    # ------------------------------------------------------------------

    def _get_api_key(self):
        if self.api_key:
            return self.api_key
        key = tools.config.get(f"llm_{self.provider_type}_api_key")
        if key:
            return key
        return os.environ.get(f"LLM_{self.provider_type.upper()}_API_KEY")

    def _get_base_url(self, default_url=""):
        return self.base_url or default_url

    # ------------------------------------------------------------------
    # Delegation to registry implementation
    # ------------------------------------------------------------------

    def _get_impl(self):
        from ..providers import PROVIDER_REGISTRY

        impl = PROVIDER_REGISTRY.get(self.provider_type)
        if not impl:
            raise UserError(
                f"No implementation registered for provider type '{self.provider_type}'"
            )
        return impl

    def get_llm(self, model, temperature=0.7):
        return self._get_impl().get_llm(self, model, temperature)

    def get_embedding(self, model):
        return self._get_impl().get_embedding(self, model)

    def discover_models(self):
        return self._get_impl().discover_models(self)

    def check_health(self):
        self._get_impl().check_health(self)

    def get_model_capabilities(self, model_name):
        return self._get_impl().get_model_capabilities(self, model_name)

    # ------------------------------------------------------------------
    # UI Actions
    # ------------------------------------------------------------------

    def action_view_models(self):
        """Open filtered model list for this provider."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"{self.name} — Models",
            "res_model": "llm.model",
            "view_mode": "list,form",
            "domain": [["provider_type", "=", self.provider_type]],
            "context": {"default_provider_type": self.provider_type},
        }

    def action_check_health(self):
        self.ensure_one()
        try:
            self.check_health()
            self.write({
                "last_health_status": "ok",
                "last_health_check": fields.Datetime.now(),
                "health_check_message": "Connection OK",
            })
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Health Check",
                    "message": f"{self.name}: Connection OK",
                    "type": "success",
                },
            }
        except Exception as e:
            self.write({
                "last_health_status": "error",
                "last_health_check": fields.Datetime.now(),
                "health_check_message": str(e)[:500],
            })
            raise UserError(f"Health check failed for {self.name}: {e}")

    def action_discover_models(self):
        self.ensure_one()
        models_data = self.discover_models()
        Model = self.env["llm.model"]

        created = 0
        updated = 0
        seen_names = set()

        for data in models_data:
            name = data["name"]
            seen_names.add(name)

            existing = Model.search([
                ("name", "=", name),
                ("provider_type", "=", self.provider_type),
            ], limit=1)

            vals = {
                "name": name,
                "provider_type": self.provider_type,
                "active": True,
                "context_window": data.get("context_window"),
                "vision": data.get("supports_vision", False),
                "tools": data.get("supports_tools", True),
                "streaming": data.get("supports_streaming", True),
                "embedding": data.get("supports_embedding", False),
                "asr": data.get("supports_asr", False),
            }

            if existing:
                existing.write(vals)
                updated += 1
            else:
                Model.create(vals)
                created += 1

        # Mark models no longer returned by API as unavailable
        Model.search([
            ("provider_type", "=", self.provider_type),
            ("name", "not in", list(seen_names)),
        ]).write({"active": False})

        # Open filtered model list for this provider
        return {
            "type": "ir.actions.act_window",
            "name": f"{self.name} — Models",
            "res_model": "llm.model",
            "view_mode": "list,form",
            "domain": [["provider_type", "=", self.provider_type]],
            "context": {
                "default_provider_type": self.provider_type,
            },
        }

# -*- coding: utf-8 -*-
"""Base model extensions — AI record serialization for context injection.

Ported from ai_agent_context/models/base_model.py.
Provides _ai_serialize_fields_data() and _ai_build_record_context()
on ALL Odoo models via base inheritance.
"""

import datetime
import json
import logging
import pytz

from odoo import models
from odoo.exceptions import AccessError
from odoo.tools.mail import html_to_inner_content

_logger = logging.getLogger(__name__)


class BaseModel(models.AbstractModel):
    """Add AI record serialization to all Odoo models."""

    _inherit = 'base'

    def _ai_truncate(self, value, size=60):
        """Limit field size to prevent prompt injection and overflow."""
        if not isinstance(value, str) or len(value) < size:
            return value
        return value[:max(0, size - 3)] + "..."

    def _ai_field_names_to_truncate(self):
        """Fields that should be truncated in AI context. Override per model."""
        return ('name', 'display_name')

    def _ai_serialize_fields_data(self, max_fields=100):
        """Return a JSON string of all non-binary record fields.

        Serializes all readable fields of the current record into a JSON
        structure suitable for inclusion in an AI system prompt.

        :return: JSON string of record field data
        """
        fields_info = self.fields_get()
        result = {}
        count = 0

        for field_name, field_attrs in fields_info.items():
            if count >= max_fields:
                break
            field_type = field_attrs.get("type", "")

            if field_type == "binary":
                continue

            try:
                field_value = self[field_name]
            except AccessError:
                continue
            except Exception:
                continue

            # Truncate specific char fields
            if field_type == 'char' and field_name in self._ai_field_names_to_truncate():
                field_value = self._ai_truncate(field_value)

            try:
                if field_type == "many2one":
                    result[field_name] = (
                        self._ai_truncate(field_value.display_name)
                        if field_value else None
                    )
                elif field_type in ("one2many", "many2many"):
                    linked_records = field_value
                    if len(linked_records) > 50:
                        continue
                    result[field_name] = [
                        self._ai_truncate(record.display_name)
                        for record in linked_records
                    ]
                elif isinstance(field_value, datetime.datetime):
                    user_tz = pytz.timezone(self.env.user.tz or 'UTC')
                    result[field_name] = (
                        field_value.astimezone(user_tz).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ) if field_value else None
                    )
                elif isinstance(field_value, datetime.date):
                    result[field_name] = (
                        field_value.strftime("%Y-%m-%d")
                        if field_value else None
                    )
                elif field_type == 'html':
                    result[field_name] = html_to_inner_content(
                        field_value or ""
                    )
                elif isinstance(field_value, models.BaseModel):
                    result[field_name] = field_value.ids
                else:
                    result[field_name] = field_value
            except Exception:
                continue

            count += 1

        return json.dumps(result, default=str, ensure_ascii=False, indent=2)

    def _ai_build_record_context(self, caller_component="quest", text_selection=None):
        """Build a context list for the AI.

        :return: List of context strings to inject into the AI prompt
        """
        context = []

        if caller_component in ("quest", "html_field_record", "chatter_ai_button"):
            context.append(
                f"You were called within an Odoo {self._name} record. "
                f"Your answers should take the record's details into account. "
                f"The following JSON contains all of the record's details:\n"
                f"```json\n{self._ai_serialize_fields_data()}\n```"
            )

        if hasattr(self, '_ai_serialize_messages_data'):
            chatter = self._ai_serialize_messages_data()
            if chatter:
                context.append(
                    f"The Odoo record, from which you were called, can also have "
                    f"associated correspondence tied to it. All those messages and "
                    f"notes are included in the chatter, a chat-like area in the "
                    f"record's form view. The previous chatter correspondence, "
                    f"from oldest to newest, for this record is this:\n{chatter}"
                )

        if caller_component == "html_field_text_select" and text_selection:
            context.append(
                f"The text that you will be rewriting is the following: "
                f"{text_selection}"
            )

        context.append(
            "ALWAYS FORMAT YOUR ANSWERS USING MARKDOWN. "
            "Avoid using HTML. Don't use unnecessary formatting like "
            "code blocks if not needed."
        )

        return context

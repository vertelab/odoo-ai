# -*- coding: utf-8 -*-
"""Company Identity Evolution — proactive mission/values updates."""

import json
import logging
from datetime import datetime, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CompanyIdentityEvolution(models.Model):
    _name = 'company.identity.evolution'
    _description = 'Company Identity Evolution'
    _abstract = True

    @api.model
    def _can_update_company(self, user=None):
        """Check if user has permission to update company mission/values."""
        if not user:
            user = self.env.user
        return user.has_group('base.group_system') or user.has_group('base.group_erp_manager')

    @api.model
    def _detect_mission_relevance(self, session, response):
        """Analyze if a conversation is relevant for mission/values updates.

        Returns dict or None:
            {has_opportunity, type, reason, suggested_update, field, confidence}
        """
        company = self.env.user.company_id
        lines = session.session_line_ids.sorted('sequence')
        if len(lines) < 3:
            return None

        conversation = '\n'.join(
            f"[{l.role}] {l.content[:300]}"
            for l in lines[-10:]  # Last 10 lines
        )

        prompt = f"""
        Compare the conversation below with the company's current
        mission and values.

        Current mission: {company.company_mission or '(not set)'}
        Current values: {company.company_values or '(not set)'}

        Conversation:
        {conversation}

        Is there a gap, deepening opportunity, or clarification that
        warrants suggesting an update to the company's mission or values?

        Return JSON or null:
        {{
            "has_opportunity": true/false,
            "type": "gap|deepening|clarification",
            "reason": "why this matters",
            "suggested_mission": "new mission text if applicable",
            "suggested_values": "new values text if applicable",
            "field": "mission|values|both",
            "confidence": 0.0-1.0
        }}
        """
        try:
            result = self.env['ai.provider']._call_llm(prompt)
            data = json.loads(result) if isinstance(result, str) else result
            return data
        except Exception as e:
            _logger.warning("Mission relevance detection failed: %s", e)
            return None

    @api.model
    def _check_thresholds(self, company):
        """Check if we can ask about mission/values updates."""
        config = self.env['ir.config_parameter'].sudo()
        interval = int(config.get_param('company.mission_review_interval_days', '30'))
        threshold = float(config.get_param('company.mission_confidence_threshold', '0.7'))
        max_requests = int(config.get_param('company.mission_max_requests_per_user', '3'))

        last_review = company.company_mission_last_review
        if last_review:
            days_since = (fields.Datetime.now() - last_review).days
            if days_since < interval:
                return False, f"Only {days_since} days since last review (need {interval})"

        return True, {'threshold': threshold, 'max_requests': max_requests}

    @api.model
    def _log_company_identity_change(self, company, field, old_value, new_value, user=None):
        """Create a company memory entry when mission/values change."""
        if not user:
            user = self.env.user
        self.env['ai.company.memory'].create({
            'company_id': company.id,
            'content': (
                f"**{field} updated**\n\n"
                f"**Previous**: {old_value[:500]}\n"
                f"**New**: {new_value[:500]}\n"
                f"**Updated by**: {user.name}\n"
                f"**Date**: {fields.Datetime.now()}"
            ),
            'category': 'management',
            'scope': 'public',
            'importance': 'high',
        })
        # Update review timestamp
        if field == 'mission':
            company.company_mission_last_review = fields.Datetime.now()
        elif field == 'values':
            company.company_values_last_review = fields.Datetime.now()

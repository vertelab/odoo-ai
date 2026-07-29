# -*- coding: utf-8 -*-
"""Discuss Learning — extract learnings from Discuss channel conversations."""

import json
import logging
from datetime import date, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class DiscussLearning(models.Model):
    _name = 'discuss.learning'
    _description = 'Discuss Learning'
    _abstract = True

    @api.model
    def cron_extract_from_discuss(self):
        """Analysera gårdagens channel-meddelanden och extrahera lärdomar.

        Körs nattetid efter cron_index_chats. För varje aktiv användare:
        1. Samla alla channel-meddelanden från gårdagen
        2. EN LLM-anrop per användare för extraction
        3. Spara i ai.personal.memory
        4. Uppdatera identity om relevant
        """
        yesterday = date.today() - timedelta(days=1)
        today = date.today()
        yesterday_start = yesterday.strftime('%Y-%m-%d 00:00:00')
        today_start = today.strftime('%Y-%m-%d 00:00:00')

        # Hitta alla channel-meddelanden från igår
        messages = self.env['mail.message'].search([
            ('model', '=', 'discuss.channel'),
            ('create_date', '>=', yesterday_start),
            ('create_date', '<', today_start),
            ('message_type', '=', 'comment'),
            ('body', '!=', False),
            ('author_id', '!=', False),
        ])
        _logger.info("Found %d discuss messages from %s", len(messages), yesterday)

        if not messages:
            return 0

        # Gruppera per användare
        by_user = {}
        for msg in messages:
            users = self.env['res.users'].search([
                ('partner_id', '=', msg.author_id.id),
            ], limit=1)
            if not users:
                continue
            user = users[0]
            if not user.learn_from_discuss:
                continue

            channel = self.env['discuss.channel'].browse(msg.res_id or 0)
            if not channel.exists() or channel.channel_type != 'channel':
                continue  # Endast publika kanaler, inte privata chattar

            by_user.setdefault(user.id, {
                'user': user,
                'messages': [],
                'channel_ids': set(),
            })
            body_text = self._html_to_text(msg.body or '')
            by_user[user.id]['messages'].append(
                f"[{channel.name}] {msg.author_id.name}: {body_text[:300]}"
            )
            by_user[user.id]['channel_ids'].add(channel.id)

        total = 0
        for uid, data in by_user.items():
            if len(data['messages']) < 3:
                continue  # För lite data för meningsfull extraction

            try:
                result = self._extract_user_learnings(
                    user=data['user'],
                    messages=data['messages'],
                )
                if result:
                    self._apply_learnings(data['user'], result)
                    total += len(result.get('memories', []))
            except Exception as e:
                _logger.error("Failed to extract learnings for user %s: %s",
                              data['user'].name, e)

        _logger.info("Extracted %d learnings from discuss channels", total)
        return total

    @api.model
    def _extract_user_learnings(self, user, messages):
        """LLM-baserad extraction av lärdomar från channel-meddelanden."""
        prompt = f"""
        Given these channel messages from {user.name}, extract:
        1. New factual memories (for ai.personal.memory)
        2. Personality/preference insights (for ai.identity)
        3. Emerging interests or goals

        Messages:
        {chr(10).join(messages[:20])}

        Return JSON:
        {{
            "memories": [
                {{"content": "...", "category": "fact|preference|goal|context", "importance": "low|medium|high"}}
            ],
            "identity_updates": {{
                "style": "updated style description or null",
                "user_model": "updated user model or null"
            }},
            "emerging_interests": ["topic1", "topic2"]
        }}
        """
        try:
            # Use the quest's LLM provider
            provider = self.env['ai.provider'].search([], limit=1)
            if not provider:
                _logger.warning("No AI provider configured for discuss learning")
                return None

            response = provider._call_llm(prompt)
            data = json.loads(response) if isinstance(response, str) else response
            return data
        except Exception as e:
            _logger.warning("LLM extraction failed: %s", e)
            return None

    @api.model
    def _apply_learnings(self, user, learnings):
        """Applicera extraherade lärdomar till användarens minne och identity."""
        memories = learnings.get('memories', [])
        for mem in memories:
            self.env['ai.personal.memory'].add_memory(
                user_id=user.id,
                content=mem['content'],
                category=mem.get('category', 'context'),
                source='discuss_chat',
                importance=mem.get('importance', 'medium'),
                company_id=user.company_id.id,
            )

        identity_updates = learnings.get('identity_updates', {})
        if identity_updates:
            quest = user.personal_quest_id
            if quest and quest.identity_id:
                identity = quest.identity_id
                if identity_updates.get('style'):
                    identity.style = identity_updates['style']
                if identity_updates.get('user_model'):
                    existing = identity.user_model or ''
                    update = identity_updates['user_model']
                    if update not in existing:
                        identity.user_model = existing + '\n' + update

    @api.model
    def _html_to_text(self, html):
        """Convert HTML to plain text."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, 'html.parser')
            for tag in soup(['script', 'style']):
                tag.decompose()
            text = soup.get_text(separator=' ', strip=True)
            return text[:2000]  # Max 2000 chars
        except ImportError:
            import re
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text).strip()
            return text[:2000]

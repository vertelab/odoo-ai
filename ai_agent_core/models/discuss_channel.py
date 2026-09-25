# -*- coding: utf-8 -*-
"""Discuss channel extensions for Buzz workspace support."""

import logging

from odoo import models, fields, api
from odoo.http import request as http_request

_logger = logging.getLogger(__name__)


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    ai_agent_ids = fields.Many2many(
        'ai.agent', 'discuss_channel_ai_agent_rel',
        'channel_id', 'agent_id',
        string='AI Agents',
        help='AI agents that are visible members of this channel via Buzz workspaces.')
    ai_coworker_ids = fields.Many2many(
        'ai.coworker', 'discuss_channel_ai_coworker_rel',
        'channel_id', 'coworker_id',
        string='AI Coworkers',
        help='AI coworkers that are visible members of this channel.')
    ai_coworker_id = fields.Many2one(
        'ai.coworker', string='Buzz Quest',
        help='The Buzz workspace quest linked to this channel, if any.')

    # ── Rekordkontext (ai-coworker-record-context 9.x) ──────────────────
    #
    # DM-/kanal-chatten har definitionsmässigt INGEN record: en DM är inte
    # en form- eller list-vy. Men användaren står ofta i en vy när hen
    # skriver i chatten, och förväntar sig samma beteende som en server
    # action (där Odoo injicerar `records` från vyn automatiskt).
    #
    # Frontend-patchen (static/src/js/ai_record_context_patch.js) läser
    # aktuell FormController/ListController och skickar model + res_id i
    # meddelandets context. Här fångas det upp och lagras på kanalen, så
    # att _detect_record() kan läsa det vid varje tur.
    #
    # Fälten ligger på KANALEN (inte sessionen) eftersom DM-turen skapar en
    # ny session varje gång — kanalen är den stabila bäraren.
    ai_context_model = fields.Char(
        string='AI Context Model',
        help='Modellen användaren hade öppen när chatten användes senast.')
    ai_context_record_id = fields.Integer(
        string='AI Context Record ID',
        help='Recorden användaren hade öppen (form-vy).')
    ai_context_record_ids = fields.Json(
        string='AI Context Record IDs',
        help='Markeringen användaren hade (list-vy).')
    ai_context_view_type = fields.Char(
        string='AI Context View Type',
        help='Vytypen (form, list, kanban, ...).')

    # ── Automatisk capture vid varje meddelande ────────────────────────

    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, **kwargs):
        """Fånga användarens vy-kontext innan meddelandet skapas.

        Körs FÖRE super() så att _detect_record() (som anropas i
        mail.message.create() → _route_message() → chat()) redan ser
        kontexten när AI-turen startar.

        OBS: frontend-patchen skickar context_model/context_res_id i
        params.context. Odoos /mail/message/post-kontroller lägger in
        dem i request.env.context (thread.py: `request.update_context
        (**context)`), så de finns i env.context här — vi behöver inte
        gräva i kwargs.
        """
        self._capture_ai_context()
        return super().message_post(**kwargs)

    def _capture_ai_context(self, extra_context=None):
        """Läs aktuell vy-kontext och lagra den på kanalen.

        Källor, i prioritetsordning:
          1. env.context — frontend-patchen skickar context_model/
             context_res_id i params.context, som Odoos
             /mail/message/post-kontroller lägger in i env.context.
          2. `extra_context` — explicit dict (t.ex. vid direkta anrop).
          3. HTTP-requesten — Odoos action-system.
          4. Redan lagrad kontext — skrivs inte över med sämre data.

        Tyst no-op vid fel: kontexten får aldrig fälla en chatt-tur.
        """
        try:
            src = self._resolve_ai_context_sources(extra_context)
        except Exception as e:
            _logger.warning('ai-context: källuppslag misslyckades: %s', e)
            return

        model = src['model']
        record_id = src['record_id']
        record_ids = src['record_ids']

        if not model:
            return

        # Skriv inte över en specifik record med enbart modell-kontext.
        if self.ai_context_model and self.ai_context_record_id \
                and not record_id and not record_ids:
            return

        vals = {}
        if model != self.ai_context_model:
            vals['ai_context_model'] = model
        if record_id and record_id != self.ai_context_record_id:
            vals['ai_context_record_id'] = int(record_id)
            vals['ai_context_record_ids'] = False
        elif record_ids and record_ids != (self.ai_context_record_ids or []):
            vals['ai_context_record_ids'] = list(record_ids)
            vals['ai_context_record_id'] = False
        if src['view_type'] and src['view_type'] != self.ai_context_view_type:
            vals['ai_context_view_type'] = src['view_type']

        if vals:
            self.sudo().write(vals)
            _logger.info(
                'ai-context: kanal %s ← model=%s record_id=%s record_ids=%s '
                'view=%s (källa: %s)',
                self.id, model, record_id, record_ids, src['view_type'],
                src['source'])

    def _resolve_ai_context_sources(self, extra_context=None):
        """Slå upp vy-kontext ur alla tillgängliga källor.

        Returnerar dict med model/record_id/record_ids/view_type/source.
        """
        result = {
            'model': None, 'record_id': None, 'record_ids': None,
            'view_type': None, 'source': None,
        }

        def _ids(value):
            """Normalisera till lista av int."""
            if value is None or value is False:
                return []
            if isinstance(value, (list, tuple)):
                raw = value
            else:
                raw = str(value).split(',')
            out = []
            for i in raw:
                try:
                    out.append(int(i))
                except (TypeError, ValueError):
                    continue
            return out

        def _apply(ctx, source):
            """Läs model/res_id ur en kontext-dict."""
            if not ctx:
                return False
            model = (ctx.get('context_model')
                     or ctx.get('context_record_model')
                     or ctx.get('active_model'))
            if not model:
                return False
            rid = ctx.get('context_res_id') or ctx.get('context_record_id')
            rids = _ids(ctx.get('context_res_ids'))
            if not rids:
                aid = ctx.get('active_id')
                aids = _ids(ctx.get('active_ids'))
                if aids:
                    rids = aids
                elif aid:
                    rid = rid or aid
            if not rid and not rids:
                return False
            result.update({
                'model': model,
                'record_id': int(rid) if rid else None,
                'record_ids': rids or None,
                'view_type': ctx.get('view_type'),
                'source': source,
            })
            return True

        # 1. env.context — frontend-patch → params.context →
        #    request.update_context() i /mail/message/post.
        if _apply(self.env.context, 'env-context'):
            return result

        # 2. Explicit dict (direkta anrop/tester).
        if _apply(extra_context, 'explicit-context'):
            return result

        # 3. HTTP-requesten.
        try:
            req = http_request
            if req:
                _apply(getattr(req, 'context', {}) or {}, 'http-context')
                if result['model']:
                    return result
                _apply(getattr(req, 'params', {}) or {}, 'http-params')
        except Exception:
            pass

        return result

    def _get_ai_context_record(self):
        """Returnera aktuell record (eller markering) för denna kanal.

        Används av ai.coworker._detect_record() / _detect_records().
        Returnerar (records, view_type) eller (tomt recordset, None).
        """
        self.ensure_one()
        empty = self.env['ai.coworker.session'].browse(0)
        model = self.ai_context_model
        if not model or model not in self.env:
            return empty, None
        ids = []
        if self.ai_context_record_ids:
            try:
                ids = [int(i) for i in self.ai_context_record_ids]
            except (TypeError, ValueError):
                ids = []
        elif self.ai_context_record_id:
            ids = [int(self.ai_context_record_id)]
        if not ids:
            return empty, None
        try:
            recs = self.env[model].browse(ids).exists()
        except Exception as e:
            _logger.warning('ai-context: kunde inte browsa %s %s: %s',
                            model, ids, e)
            return empty, None
        return recs, self.ai_context_view_type

    def _sync_ai_agent_members(self):
        """Ensure ai.agent partners are present in channel members."""
        for channel in self:
            if channel.channel_type != 'channel':
                continue
            current_partners = channel.channel_member_ids.mapped('partner_id')
            for agent in channel.ai_agent_ids:
                if agent.partner_id and agent.partner_id not in current_partners:
                    self.env['discuss.channel.member'].sudo().create({
                        'channel_id': channel.id,
                        'partner_id': agent.partner_id.id,
                    })

    def _sync_ai_coworker_members(self):
        """Ensure ai.coworker partners are present in channel members."""
        for channel in self:
            if channel.channel_type != 'channel':
                continue
            current_partners = channel.channel_member_ids.mapped('partner_id')
            for coworker in channel.ai_coworker_ids:
                coworker._ensure_partner()
                if coworker.partner_id and coworker.partner_id not in current_partners:
                    self.env['discuss.channel.member'].sudo().create({
                        'channel_id': channel.id,
                        'partner_id': coworker.partner_id.id,
                    })

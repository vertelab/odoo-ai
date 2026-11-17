# -*- coding: utf-8 -*-
"""
Context bridge — start a DM/kanal med den record användaren har framför sig.

Ported from ai_agent_context/controllers/main.py (2026-11-16).

SYFTE
=====
Användaren står i en helpdesk-ticket (form) eller har markerat rader i en
list. Hen vill ställa frågor OM den recorden i en DM/chatt med en AI-
medarbetare — utan att klistra in fältvärden.

Flödet:
  1. Frontend läser aktuell vy (model, resId, fältvärden) ur FormController.
  2. POST /ai/context/launch  →  skapar DM-kanal + session, sätter
     kanalens vy-kontext och kör coworkern med recorden.
  3. POST /ai/context/set     →  uppdaterar kanalens kontext utan att
     starta en ny tur (användaren byter record i samma chatt).

Skillnad mot powerbox (/ai/powerbox/*)
=====================================
Powerbox är en slash-kommando INUTI ett textfält: välj quest → resultatet
skrivs i fältet. Det är inline-textgenerering, inget samtal.

Den här bryggan startar ett SAMTAL. Recorden följer med som kontext så
användaren kan ställa följdfrågor i DM:en.

Skillnad mot ai_record_context_patch.js
=======================================
Patchen fångar automatiskt vyn vid VARJE meddelande i en redan befintlig
kanal. Den här bryggan SKAPAR kanalen (när den inte finns) och sätter
kontexten explicit — den behövs för första turen, och för att byta record.

Kontexten lagras på kanalen (ai_context_*) och läses av
discuss.channel._get_ai_context_record(), som ai.coworker._detect_record()
använder (källa 4a).
"""

import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class AIContextBridge(http.Controller):
    """Endpoints för systray-knappen och form-controller-patchen."""

    # ── Hjälpare ──────────────────────────────────────────────────────

    def _get_or_create_channel(self, coworker):
        """Hitta (eller skapa) DM-kanalen för coworkern och användaren.

        Återanvänder coworkerns init_type-kanal när den finns — samma kanal
        som @mention-routing använder — så vi inte skapar en ny DM per
        klick.
        """
        channel = False
        init_types = coworker.init_type_ids
        for it in init_types:
            if it.channel_id:
                channel = it.channel_id
                break
        if not channel and init_types:
            it = init_types[0]
            it._ensure_channel()
            channel = it.channel_id

        if not channel:
            channel = request.env['discuss.channel'].sudo().create({
                'name': coworker.name,
            })
            channel.write({'ai_coworker_ids': [(4, coworker.id)]})
            try:
                channel._sync_ai_coworker_members()
            except Exception as e:
                _logger.debug('context_bridge: medlemssynk hoppades över: %s', e)

        # Se till att användaren är medlem, annars syns inte kanalen.
        partner = request.env.user.partner_id
        if partner and partner not in channel.channel_member_ids.partner_id:
            try:
                channel.add_members(partner_ids=partner.ids)
            except Exception as e:
                _logger.debug('context_bridge: add_members hoppades över: %s', e)
        return channel

    def _set_channel_context(self, channel, model, res_id, res_ids,
                             view_type, record_data):
        """Skriv vy-kontexten på kanalen.

        Går via _capture_ai_context (samma väg som meddelande-patchen) så
        att "skriv inte över specifik record med bara modell"-regeln gäller.
        """
        # _resolve_ai_context_sources läser context_*-nycklarna (samma
        # konvention som frontend-patchen skickar i params.context).
        extra = {
            'context_model': model,
            'context_res_id': int(res_id) if res_id else False,
            'context_res_ids': [int(x) for x in res_ids] if res_ids else False,
            'view_type': view_type,
        }
        channel._capture_ai_context(extra_context=extra)

        # Spara fältvärdena (osparade ändringar från frontend) på sessionen
        # så coworkern ser dem även om recorden inte är sparad än.
        if record_data and model:
            self._store_snapshot(channel, model, res_id, res_ids, record_data)

    def _store_snapshot(self, channel, model, res_id, res_ids, record_data):
        """Delegera till kanalmodellen (testbart utan HTTP-request)."""
        return channel._store_ai_context_snapshot(
            model, res_id, res_ids, record_data)

    # ── Endpoints ─────────────────────────────────────────────────────

    @http.route('/ai/context/set', type='jsonrpc', auth='user',
                methods=['POST'], csrf=False, sitemap=False)
    def set_context(self, channel_id=None, coworker_id=None, model=None,
                    res_id=None, res_ids=None, view_type=None,
                    record_data=None, **kw):
        """Sätt/uppdatera kanalens vy-kontext utan att köra en tur.

        Anropas när användaren byter record medan chatten är öppen.
        """
        channel = False
        if channel_id:
            channel = request.env['discuss.channel'].browse(int(channel_id))
            if not channel.exists():
                return {'success': False, 'error': 'Channel not found'}
        elif coworker_id:
            coworker = request.env['ai.coworker'].browse(int(coworker_id))
            if not coworker.exists():
                return {'success': False, 'error': 'Coworker not found'}
            channel = self._get_or_create_channel(coworker)
        if not channel:
            return {'success': False, 'error': 'channel_id or coworker_id required'}

        self._set_channel_context(channel, model, res_id, res_ids,
                                  view_type, record_data)
        return {'success': True, 'channel_id': channel.id}

    @http.route('/ai/context/launch', type='jsonrpc', auth='user',
                methods=['POST'], csrf=False, sitemap=False)
    def launch(self, coworker_id, model=None, res_id=None, res_ids=None,
               view_type=None, record_data=None, prompt=None, **kw):
        """Starta en DM med coworkern och recorden som kontext.

        Skapar kanalen om den inte finns, sätter kontexten och kör en
        första tur om `prompt` anges.
        """
        coworker = request.env['ai.coworker'].browse(int(coworker_id))
        if not coworker.exists():
            return {'success': False, 'error': 'Coworker not found'}

        channel = self._get_or_create_channel(coworker)
        self._set_channel_context(channel, model, res_id, res_ids,
                                  view_type, record_data)

        session = False
        try:
            session = request.env['ai.coworker.session']._find_or_create_coworker_session(
                coworker_id=coworker.id,
                user_id=request.env.user.id,
                prompt=prompt or '',
            )
        except Exception as e:
            _logger.warning('context_bridge: session hittades ej: %s', e)

        reply = False
        if prompt:
            try:
                records = False
                if model and res_id:
                    records = request.env[model].browse(int(res_id))
                reply = coworker.run(
                    prompt,
                    session=session or None,
                    channel=channel,
                    records=records or None,
                )
            except Exception as e:
                _logger.error('context_bridge: körning misslyckades: %s',
                              e, exc_info=True)
                return {
                    'success': False,
                    'error': str(e),
                    'channel_id': channel.id,
                    'session_id': session.id if session else False,
                }

        return {
            'success': True,
            'channel_id': channel.id,
            'session_id': session.id if session else False,
            'reply': reply or '',
        }

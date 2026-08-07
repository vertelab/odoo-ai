# -*- coding: utf-8 -*-
"""OpenAI-compatible API controller for ai.coworker.

Exposes /ai/openai/<coworker_id>/v1/chat/completions in OpenAI format
so external tools (Cline, Continue.dev) can use coworkers as drop-in
replacements for OpenAI.
"""

import asyncio
import base64
import json
import logging
import re
import time
from odoo import http, fields
from odoo.http import request, Response

_logger = logging.getLogger(__name__)

# Simple in-memory rate limiter (per coworker_id)
_rate_limiters = {}


class _RateLimiter:
    """Token-bucket rate limiter for rpm/tpm."""

    def __init__(self, rpm=30, tpm=100000):
        self.rpm = rpm
        self.tpm = tpm
        self.req_timestamps = []
        self.token_count = 0
        self.token_window_start = time.time()

    def check(self, tokens=0):
        now = time.time()
        # Clean old request timestamps
        self.req_timestamps = [t for t in self.req_timestamps if now - t < 60]
        if len(self.req_timestamps) >= self.rpm:
            return False, 60 - (now - self.req_timestamps[0])
        # Check tpm
        if now - self.token_window_start > 60:
            self.token_count = 0
            self.token_window_start = now
        if self.token_count + tokens > self.tpm:
            return False, 60 - (now - self.token_window_start)
        return True, 0


class AIOpenAPIController(http.Controller):
    """OpenAI-compatible chat completions endpoint."""

    @http.route('/ai/openai/<int:coworker_id>/v1/chat/completions',
                type='http', auth='none',
                methods=['POST'], csrf=False, sitemap=False)
    def chat_completions(self, coworker_id, **kw):
        """OpenAI-compatible chat completions endpoint.

        Supports both streaming and non-streaming responses.
        """
        # Auth — validate user API key and set request user
        auth_header = request.httprequest.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return self._error(401, "Invalid API key")

        api_key = auth_header[7:]
        try:
            user_id = request.env['res.users.apikeys'].sudo()._check_credentials(
                scope='rpc', key=api_key)
            if user_id:
                request.update_env(user=user_id)
            else:
                return self._error(401, "Invalid API key")
        except Exception:
            return self._error(401, "Invalid API key")

        # Find coworker WITHOUT sudo — request.env.user is the API-authenticated
        # user (set via request.update_env above). Using .sudo() would discard
        # the user identity and make _build_injection_prompt() see SUPERUSER.
        coworker = request.env['ai.coworker'].browse(coworker_id)
        if not coworker.exists():
            return self._error(404, "Coworker not found")

        # Verify coworker has openai_api enabled (sudo for init_type_ids read access)
        oai_init = coworker.sudo().init_type_ids.filtered(
            lambda it: it.init_type == 'openai_api' and it.enabled
        )
        if not oai_init:
            return self._error(404, "OpenAI API not configured for this coworker")

        # Parse request body
        try:
            body = json.loads(request.httprequest.data or '{}')
        except json.JSONDecodeError:
            return self._error(400, "Invalid JSON")

        messages = body.get('messages', [])
        stream = body.get('stream', False)
        model = body.get('model', '')
        tools = body.get('tools', [])
        temperature = body.get('temperature', 0.7)
        max_tokens = body.get('max_tokens', 4096)
        user_ident = body.get('user', '')
        pi_session_id = (body.get('pi_session_id') or '').strip()
        session_id = int(body.get('session_id') or 0)

        if not messages:
            return self._error(400, "Missing messages")

        # Rate limiting
        rpm = oai_init[0].rate_limit_rpm or 30
        tpm = oai_init[0].rate_limit_tpm or 100000
        limiter = _rate_limiters.setdefault(
            coworker_id, _RateLimiter(rpm=rpm, tpm=tpm)
        )

        # Estimate tokens from messages (rough: 4 chars ≈ 1 token)
        prompt_text = json.dumps(messages)
        estimated_tokens = len(prompt_text) // 4
        allowed, retry_after = limiter.check(estimated_tokens)
        if not allowed:
            return self._error(429, "Rate limit exceeded", retry_after=retry_after)

        # Resolve user for personal memory (optional)
        resolved_user = None
        if user_ident:
            if user_ident.startswith('res.users:'):
                uid = int(user_ident.split(':')[1])
                resolved_user = request.env['res.users'].sudo().browse(uid)

        # Build prompt from messages
        prompt = self._messages_to_prompt(messages)

        # Build system prompt with user identity and memory injection
        # (same pattern as stream.py — agent-memory-governance 3.x).
        # Uses request.env.user (the API-authenticated user, not SUPERUSER)
        # so the coworker knows who it's talking to.
        system_prompt = coworker.description or ''
        try:
            injection = coworker._build_injection_prompt(
                user=request.env.user, prompt=prompt)
            if injection:
                system_prompt = (system_prompt + '\n\n' + injection).strip()
        except Exception as e:
            _logger.warning('OpenAI API injection failed: %s', e)

        if stream:
            return self._handle_stream(
                coworker, prompt, system_prompt, model, temperature,
                max_tokens, tools, estimated_tokens,
                pi_session_id=pi_session_id, session_id=session_id,
            )
        else:
            return self._handle_sync(
                coworker, prompt, system_prompt, model, temperature,
                max_tokens, tools, estimated_tokens,
                pi_session_id=pi_session_id, session_id=session_id,
            )

    def _find_or_create_session(self, coworker, prompt, pi_session_id='',
                                session_id=0):
        """Hitta/skapa session via pi_session_id (Pi) eller återanvänd
        session_id (session-cost-context 3.1). Sätter context-nycklarna så
        verktyg (cost_context_get/set) når sessionen."""
        Sess = coworker.env['ai.coworker.session']
        sess = Sess.browse(0)
        if session_id:
            sess = Sess.browse(int(session_id))
            if not sess.exists():
                sess = Sess.browse(0)
        if not sess and pi_session_id:
            sess = Sess.search([('pi_session_id', '=', pi_session_id)],
                               limit=1)
        if not sess:
            sess = Sess.create({
                'coworker_id': coworker.id, 'status': 'active',
                'name': (prompt or 'API')[:50],
                'user_id': coworker.env.user.id,
                'pi_session_id': pi_session_id or False,
            })
        # Domän-ren hook: bryggor fångar domänkontext
        try:
            sess._session_capture_context()
            sess._session_auto_capture(prompt)
        except Exception as e:
            _logger.warning('session capture failed: %s', e)
        return sess

    def _session_context_env(self, coworker, sess):
        """Coworker med context-nycklar som pekar på sessionen (verktyg)."""
        return coworker.with_context(
            _ai_context_model='ai.coworker.session',
            _ai_context_id=sess.id,
            ai_lineage_session_id=sess.id,
        )

    def _cost_context_prompt_block(self, coworker, sess):
        """Bygg kostnadskontext-blocket (D9) för systemprompten.

        Injiceras endast för openai_api-körningar. Innehåller aktuell
        session-kontext + coworkerns konfigurerade frågetext +
        "fråga en gång"-instruktion. Tyst no-op vid fel.
        """
        try:
            oai = coworker.sudo().init_type_ids.filtered(
                lambda it: it.init_type == 'openai_api' and it.enabled)
            if not oai:
                return ''
            cc = []
            if 'project_id' in sess._fields and sess.project_id:
                cc.append(f'projekt: {sess.project_id.name} '
                          f'(id={sess.project_id.id})')
            if 'task_id' in sess._fields and sess.task_id:
                cc.append(f'uppgift: {sess.task_id.name} '
                          f'(id={sess.task_id.id})')
            if sess.partner_id:
                cc.append(f'kund: {sess.partner_id.name} '
                          f'(id={sess.partner_id.id})')
            confirmed = sess.cost_context_confirmed
            question = (coworker.cost_context_question or '').strip()
            block = (
                '\n\n## Kostnadskontext\n'
                'Aktuell kontext: '
                f'{chr(44).join(cc) if cc else "ingen (saknas)"}'
                f'. Bekräftad: {"ja" if confirmed else "nej"}.\n'
            )
            if not confirmed:
                block += (
                    'Alla sessioner är kostnadsbärande arbete. Om '
                    'kostnadskontext saknas: ställ frågan nedan till '
                    'användaren, EN gång per session.\n'
                    + (f'Fråga: "{question}"\n' if question else
                       'Fråga: "Vilket projekt gäller detta arbete? '
                       '(eller kund om projekt saknas)"\n')
                    + 'När användaren svarat: bekräfta att kostnaden '
                      'belastar projektet/kunden och anropa '
                      'cost_context_set (kolla först med cost_context_get). '
                      'Ställ ALDRIG om frågan när cost_context_confirmed '
                      'är satt.\n'
                )
            else:
                block += 'Fråga inte om kostnadskontext igen.\n'
            return block
        except Exception as e:
            _logger.warning('cost-context injection failed: %s', e)
            return ''

    def _cost_context_payload(self, sess):
        return {
            'project_id': (
                sess.project_id.id
                if 'project_id' in sess._fields and sess.project_id
                else None),
            'task_id': (
                sess.task_id.id
                if 'task_id' in sess._fields and sess.task_id
                else None),
            'partner_id': sess.partner_id.id if sess.partner_id else None,
            'cost_context_confirmed': sess.cost_context_confirmed,
        }

    def _handle_sync(self, coworker, prompt, system_prompt, model,
                     temperature, max_tokens, tools, estimated_tokens,
                     pi_session_id='', session_id=0):
        """Handle non-streaming request — run AgentLoop and return JSON."""
        try:
            # Run WITHOUT sudo — coworker.env carries the API-authenticated
            # user so session.user_id is correct (not SUPERUSER).
            _session = self._find_or_create_session(
                coworker, prompt, pi_session_id=pi_session_id,
                session_id=session_id)
            coworker = self._session_context_env(coworker, _session)
            system_prompt = (system_prompt or '') + \
                self._cost_context_prompt_block(coworker, _session)
            result = coworker.run(
                prompt=prompt,
                system_prompt=system_prompt,
                session=_session,
            )
            # Personligt lärande (OpenAI API-yta)
            try:
                coworker._maybe_learn_async(_session.id)
            except Exception:
                pass

            response_text = result.text if hasattr(result, 'text') else str(result or '')
            input_t = getattr(result, 'input_tokens', estimated_tokens)
            output_t = getattr(result, 'output_tokens', len(response_text) // 4)

            return Response(json.dumps({
                'id': f'chatcmpl-{coworker.id}-{int(time.time())}',
                'object': 'chat.completion',
                'created': int(time.time()),
                'model': model or 'default',
                'choices': [{
                    'index': 0,
                    'message': {'role': 'assistant', 'content': response_text},
                    'finish_reason': 'stop',
                }],
                'usage': {
                    'prompt_tokens': input_t,
                    'completion_tokens': output_t,
                    'total_tokens': input_t + output_t,
                },
                'session_id': _session.id,
                'cost_context': self._cost_context_payload(_session),
            }), content_type='application/json')

        except Exception as e:
            _logger.error('OpenAI API sync error: %s', e, exc_info=True)
            return self._error(500, str(e))

    def _handle_stream(self, coworker, prompt, system_prompt, model,
                       temperature, max_tokens, tools, estimated_tokens,
                       pi_session_id='', session_id=0):
        """Handle streaming request — SSE response."""
        response_id = f'chatcmpl-{coworker.id}-{int(time.time())}'
        created = int(time.time())
        _session = self._find_or_create_session(
            coworker, prompt, pi_session_id=pi_session_id,
            session_id=session_id)
        coworker = self._session_context_env(coworker, _session)
        system_prompt = (system_prompt or '') + \
            self._cost_context_prompt_block(coworker, _session)

        def generate():
            try:
                # Run WITHOUT sudo — coworker.env carries the API-authenticated
                # user so session.user_id is correct (not SUPERUSER).
                result = coworker.run(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    session=_session,
                )
                # Personligt lärande (OpenAI API-yta)
                try:
                    coworker._maybe_learn_async(_session.id)
                except Exception:
                    pass
                response_text = result.text if hasattr(result, 'text') else str(result or '')

                # Stream token by token
                words = response_text.split(' ')
                for i, word in enumerate(words):
                    chunk = word + (' ' if i < len(words) - 1 else '')
                    yield f'data: {json.dumps({"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model or "default", "choices": [{"index": 0, "delta": {"content": chunk}}]})}\n\n'

                yield f'data: {json.dumps({"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model or "default", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})}\n\n'
                # Session-info (session-cost-context 3.3) — före [DONE]
                yield f'data: {json.dumps({"session_id": _session.id, "cost_context": self._cost_context_payload(_session)})}\n\n'
                yield 'data: [DONE]\n\n'

            except Exception as e:
                _logger.error('OpenAI API stream error: %s', e, exc_info=True)
                yield f'data: {json.dumps({"error": {"message": str(e)}})}\n\n'
                yield 'data: [DONE]\n\n'

        return Response(
            generate(),
            content_type='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',
            },
        )

    def _messages_to_prompt(self, messages):
        """Convert OpenAI message format to plain prompt."""
        parts = []
        for msg in messages:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            if isinstance(content, list):
                # Handle multimodal content arrays
                texts = [
                    c['text'] for c in content if c.get('type') == 'text'
                ]
                content = '\n'.join(texts)
            if role == 'system':
                parts.append(f'[System]\n{content}')
            elif role == 'user':
                parts.append(f'[User]\n{content}')
            elif role == 'assistant':
                parts.append(f'[Assistant]\n{content}')
        return '\n\n'.join(parts)

    def _error(self, status, message, retry_after=None):
        headers = {'Content-Type': 'application/json'}
        if retry_after:
            headers['Retry-After'] = str(int(retry_after))
        return Response(
            json.dumps({"error": {"message": message, "type": "error"}}),
            status=status, headers=headers,
        )

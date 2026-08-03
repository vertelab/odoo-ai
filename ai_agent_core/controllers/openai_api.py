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
        # Auth
        auth_header = request.httprequest.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return self._error(401, "Invalid API key")

        api_key = auth_header[7:]
        coworker = request.env['ai.coworker'].sudo().browse(coworker_id)
        if not coworker.exists():
            return self._error(404, "Coworker not found")

        # Find openai_api init_type and validate key
        oai_init = coworker.init_type_ids.filtered(
            lambda it: it.init_type == 'openai_api' and it.enabled
        )
        if not oai_init:
            return self._error(404, "OpenAI API not configured for this coworker")

        # Validate API key from attachment
        if oai_init[0].api_key_attachment_id:
            stored_key = base64.b64decode(
                oai_init[0].api_key_attachment_id.datas or b''
            ).decode('utf-8', errors='ignore').strip()
            if api_key != stored_key:
                return self._error(401, "Invalid API key")

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

        if stream:
            return self._handle_stream(
                coworker, prompt, model, temperature, max_tokens,
                tools, estimated_tokens,
            )
        else:
            return self._handle_sync(
                coworker, prompt, model, temperature, max_tokens,
                tools, estimated_tokens,
            )

    def _handle_sync(self, coworker, prompt, model, temperature,
                     max_tokens, tools, estimated_tokens):
        """Handle non-streaming request — run AgentLoop and return JSON."""
        try:
            result = coworker.sudo().run(
                prompt=prompt,
                system_prompt=coworker.description or '',
            )

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
            }), content_type='application/json')

        except Exception as e:
            _logger.error('OpenAI API sync error: %s', e, exc_info=True)
            return self._error(500, str(e))

    def _handle_stream(self, coworker, prompt, model, temperature,
                       max_tokens, tools, estimated_tokens):
        """Handle streaming request — SSE response."""
        response_id = f'chatcmpl-{coworker.id}-{int(time.time())}'
        created = int(time.time())

        def generate():
            try:
                result = coworker.sudo().run(
                    prompt=prompt,
                    system_prompt=coworker.description or '',
                )
                response_text = result.text if hasattr(result, 'text') else str(result or '')

                # Stream token by token
                words = response_text.split(' ')
                for i, word in enumerate(words):
                    chunk = word + (' ' if i < len(words) - 1 else '')
                    yield f'data: {json.dumps({"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model or "default", "choices": [{"index": 0, "delta": {"content": chunk}}]})}\n\n'

                yield f'data: {json.dumps({"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model or "default", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})}\n\n'
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

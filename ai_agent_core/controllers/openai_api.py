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


async def _collect_async(agen):
    """Collect all items from an async generator into a list."""
    result = []
    async for item in agen:
        result.append(item)
    return result


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

        # Budgetcheck (budget-hard-cap D4): hårt stopp — 429 + OpenAI-
        # kompatibel felstruktur. Notis en gång per månad.
        coworker._unlock_budget_activities()
        if coworker.budget_exhausted:
            coworker.check_cap()
            return self._error(
                429,
                'Budget slut: AI-medarbetaren har nått månadstaket. '
                'Höj taket i inställningarna eller vänta till nästa månad.',
                error_type='insufficient_quota',
                code='budget_exhausted',
            )

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
                messages=messages,
            )
        else:
            return self._handle_sync(
                coworker, prompt, system_prompt, model, temperature,
                max_tokens, tools, estimated_tokens,
                pi_session_id=pi_session_id, session_id=session_id,
                messages=messages,
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
                     pi_session_id='', session_id=0, messages=None):
        """Handle non-streaming request — run AgentLoop and return JSON."""
        from odoo.addons.ai_agent_core.core.interrupt import (
            AgentLoopPaused, OpenAIInterruptHandler)
        try:
            # Run WITHOUT sudo — coworker.env carries the API-authenticated
            # user so session.user_id is correct (not SUPERUSER).
            _session = self._find_or_create_session(
                coworker, prompt, pi_session_id=pi_session_id,
                session_id=session_id)
            coworker = self._session_context_env(coworker, _session)
            system_prompt = (system_prompt or '') + \
                self._cost_context_prompt_block(coworker, _session)

            # Återskapa Message-historik från klientens konversation
            # (bevarar assistant-tool_calls + tool-resultat som par).
            history = self._body_to_messages(messages or [])
            # HITL: pausa loopen via tool_calls istället för auto-approve.
            handler = OpenAIInterruptHandler()

            try:
                result = coworker.run_with_history(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    history=history,
                    interrupt_handler=handler,
                    session=_session,
                )
            except AgentLoopPaused as pause:
                # HITL: returnera tool_calls till klienten (pi) — loopen
                # återupptas när klienten svarar med role:"tool"-meddelanden
                # i nästa request.
                _logger.info(
                    'OpenAI API HITL paus (%s) — returnerar tool_calls',
                    pause.state.get('kind'))
                return Response(json.dumps({
                    'id': f'chatcmpl-{coworker.id}-{int(time.time())}',
                    'object': 'chat.completion',
                    'created': int(time.time()),
                    'model': model or 'default',
                    'choices': [{
                        'index': 0,
                        'message': {
                            'role': 'assistant',
                            'content': None,
                            'tool_calls': pause.tool_calls,
                        },
                        'finish_reason': 'tool_calls',
                    }],
                    'usage': {
                        'prompt_tokens': estimated_tokens,
                        'completion_tokens': 0,
                        'total_tokens': estimated_tokens,
                    },
                    'session_id': _session.id,
                    'cost_context': self._cost_context_payload(_session),
                }), content_type='application/json')

            # Personligt lärande (OpenAI API-yta)
            try:
                coworker._maybe_learn_async(_session.id)
            except Exception:
                pass

            response_text = result.text if hasattr(result, 'text') else str(result or '')
            input_t = getattr(result, 'input_tokens', estimated_tokens)
            output_t = getattr(result, 'output_tokens', len(response_text) // 4)

            # Klientstyrning (D7): system_prompt_add + skill_to_load
            try:
                pi_instr = coworker._build_pi_instruction(_session)
                skill_to_load = coworker._pi_skill_to_load()
            except Exception as e:
                _logger.warning('pi_instruction failed: %s', e)
                pi_instr = ''
                skill_to_load = ''

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
                'system_prompt_add': pi_instr or '',
                'skill_to_load': skill_to_load or '',
            }), content_type='application/json')

        except Exception as e:
            _logger.error('OpenAI API sync error: %s', e, exc_info=True)
            return self._error(500, str(e))

    def _handle_stream(self, coworker, prompt, system_prompt, model,
                       temperature, max_tokens, tools, estimated_tokens,
                       pi_session_id='', session_id=0, messages=None):
        """Handle streaming request — SSE response."""
        response_id = f'chatcmpl-{coworker.id}-{int(time.time())}'
        created = int(time.time())
        _session = self._find_or_create_session(
            coworker, prompt, pi_session_id=pi_session_id,
            session_id=session_id)
        coworker = self._session_context_env(coworker, _session)
        system_prompt = (system_prompt or '') + \
            self._cost_context_prompt_block(coworker, _session)

        # HITL-handler + återskapad historik (samma mönster som sync)
        from odoo.addons.ai_agent_core.core.interrupt import (
            AgentLoopPaused, OpenAIInterruptHandler)
        history = self._body_to_messages(messages or [])
        handler = OpenAIInterruptHandler()

        # Fånga env-data INNAN generatorn körs (request är borta under
        # SSE-strömning) — samma mönster som /ai/stream. ORM görs om i
        # generatorn via en färsk registry-cursor.
        gen_dbname = request.env.cr.dbname
        gen_uid = request.env.uid
        gen_context = dict(request.env.context)
        _coworker_id = coworker.id
        _session_id = _session.id
        _cost = self._cost_context_payload(_session)
        _sys_prompt = system_prompt or ''
        _model = model or 'default'

        def generate():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    async def _stream(gen_env):
                        from odoo.addons.ai_agent_core.core.provider import (
                            ProviderFactory)
                        from odoo.addons.ai_agent_core.core.loop import (
                            StreamingAgentLoop, AgentConfig)
                        from odoo.addons.ai_agent_core.core.tools import (
                            ToolRegistry, ai_tool_records_to_tools)

                        def _chunk(delta, finish_reason=''):
                            d = {"id": response_id, "object": "chat.completion.chunk",
                                 "created": created, "model": model_name,
                                 "choices": [{"index": 0, "delta": delta}]}
                            if finish_reason:
                                d["choices"][0]["finish_reason"] = finish_reason
                            return f'data: {json.dumps(d)}\n\n'

                        provider, pmodel = ProviderFactory.from_coworker(
                            gen_env['ai.coworker'].browse(_coworker_id))
                        if not provider:
                            yield f'data: {json.dumps({"error": {"message": "Ingen AI-leverantör konfigurerad för denna medarbetare"}})}\n\n'
                            yield 'data: [DONE]\n\n'
                            return

                        # Faktisk modell löses från coworker-agentkedjan
                        # (ai.coworker→ai.agent→ai.model), inte från body:s
                        # `model` — samma som run_with_history/providern gör.
                        try:
                            model_name = pmodel._get_api_name() if pmodel else (_model or 'default')
                        except Exception:
                            model_name = _model or 'default'

                        tools_reg = ToolRegistry()
                        try:
                            quest = gen_env['ai.coworker'].sudo().browse(_coworker_id)
                            tool_ids = quest._session_tool_ids(
                                access_groups=gen_env.user.groups_id.ids)
                            if tool_ids:
                                tool_recs = gen_env['ai.tool'].sudo().browse(tool_ids)
                                if tool_recs:
                                    tools_reg.register_many(ai_tool_records_to_tools(tool_recs, gen_env))
                        except Exception:
                            _logger.warning('tool setup failed', exc_info=True)

                        loop_obj = StreamingAgentLoop(
                            provider=provider, tools=tools_reg,
                            interrupt_handler=handler,
                            config=AgentConfig(
                                model=model_name, system_prompt=_sys_prompt,
                                max_rounds=10,
                            ),
                        )

                        saw_tool_call = False
                        finish = 'stop'
                        try:
                            async for event in loop_obj.run_stream(prompt, history=history):
                                if event.type == "thinking":
                                    # Modellens reasoning → delta.reasoning_content
                                    # så att Pi/openai-klienter visar tänket precis
                                    # som mot bifrost direkt.
                                    yield _chunk({"reasoning_content": event.token})
                                elif event.type == "token":
                                    yield _chunk({"content": event.token})
                                elif event.type in ("tool_call_start", "tool_call_end"):
                                    saw_tool_call = True
                                    finish = 'tool_calls'
                                    tc = event.tool_call or {}
                                    yield _chunk({
                                        "role": "assistant",
                                        "tool_calls": [{
                                            "index": 0,
                                            "id": tc.id if hasattr(tc, 'id') else None,
                                            "type": "function",
                                            "function": {
                                                "name": tc.name if hasattr(tc, 'name') else '',
                                                "arguments": json.dumps(getattr(tc, 'arguments', {}) or {}),
                                            },
                                        }],
                                    })
                                elif event.type == "done":
                                    finish = event.finish_reason or ('tool_calls' if saw_tool_call else 'stop')
                        except AgentLoopPaused as pause:
                            # HITL: returnera tool_calls som SSE — klienten
                            # visar flödet och svarar med role:"tool" i nästa request.
                            tool_calls = pause.tool_calls if hasattr(pause, 'tool_calls') else []
                            if tool_calls:
                                yield _chunk({
                                    "role": "assistant",
                                    "tool_calls": [{
                                        "index": i,
                                        "id": tc.get('id'),
                                        "type": "function",
                                        "function": {"name": tc.get('name', ''), "arguments": json.dumps(tc.get('arguments', {}) or {})},
                                    } for i, tc in enumerate(tool_calls)],
                                })
                            finish = 'tool_calls'

                        yield _chunk({}, finish_reason=finish)
                        # Session-info (session-cost-context 3.3) — före [DONE]
                        yield f'data: {json.dumps({"session_id": _session_id, "cost_context": _cost})}\n\n'
                        yield 'data: [DONE]\n\n'

                    from odoo import api as _api, registry as _registry
                    with _registry(gen_dbname).cursor() as gen_cr:
                        gen_env = _api.Environment(gen_cr, gen_uid, gen_context)
                        results = loop.run_until_complete(
                            _collect_async(_stream(gen_env)))
                    for chunk in results:
                        yield chunk
                finally:
                    loop.close()
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

    def _body_to_messages(self, messages):
        """Konvertera OpenAI-konversation → core.Message-lista.

        Bevarar assistant-tool_calls + tool-resultat som par (krävs av
        providern vid replay — annars 400). System-meddelanden hanteras
        separat (skickas som system_prompt, inte i historiken).
        """
        from odoo.addons.ai_agent_core.core.provider import Message, Role
        result = []
        for m in messages:
            role = m.get('role', '')
            content = m.get('content', '')
            if isinstance(content, list):
                # Multimodala content-arrayer — ta text
                texts = [
                    c['text'] for c in content if c.get('type') == 'text'
                ]
                content = '\n'.join(texts)

            if role == 'system':
                # System-meddelanden hanteras via system_prompt-parametern,
                # inte i historiken (AgentLoop bygger messages internt).
                continue

            if role == 'assistant':
                tool_calls = m.get('tool_calls') or None
                result.append(Message(
                    role=Role.ASSISTANT,
                    content=content or '',
                    tool_calls=tool_calls,   # ← BEVARAS! dicts i OpenAI-format
                ))
            elif role == 'tool':
                result.append(Message(
                    role=Role.TOOL,
                    content=content or '',
                    tool_call_id=m.get('tool_call_id') or '',
                    name=m.get('name') or '',
                ))
            elif role == 'user':
                result.append(Message(role=Role.USER, content=content or ''))
            # okända roller ignoreras
        return result

    def _error(self, status, message, retry_after=None, error_type="error",
               code=None):
        headers = {'Content-Type': 'application/json'}
        if retry_after:
            headers['Retry-After'] = str(int(retry_after))
        error = {"message": message, "type": error_type}
        if code:
            error["code"] = code
        return Response(
            json.dumps({"error": error}),
            status=status, headers=headers,
        )

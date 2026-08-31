# -*- coding: utf-8 -*-
"""
SSE Streaming Controller for AI.Quest responses.

Token-by-token streaming via Server-Sent Events.
Uses real AIProvider + StreamingAgentLoop (no mock).
"""

import asyncio
import base64
import json
import logging
import threading
import time
from html import escape

from odoo import http, fields, api
from odoo.http import request, Response

# Import access control helper (quest-access-control change)
# Fånga ALLA undantag: vid tidig import (stream.py → ai_coworker → models)
# kan AssertionError uppstå (base_sparse_field ej laddad), vilket annars
# dödar hela controllers-paketet (openai_api/webhook/stream = /ai/* 404).
try:
    from odoo.addons.ai_agent_core.models.ai_coworker import _quest_is_accessible
except Exception:
    _quest_is_accessible = None

# Import providers at module level so every handler (SSE stream, OpenAI API,
# webhook) can construct AIProvider without NameError.
try:
    from odoo.addons.ai_agent_core.core.provider import (
        ProviderFactory, get_default_provider)
except Exception:
    ProviderFactory = get_default_provider = None

_logger = logging.getLogger(__name__)


def _content_to_text(content):
    """Normalisera OpenAI content till ren text.

    Pi och andra OpenAI-klienter skickar content antingen som sträng
    eller som multimodal array (t.ex. [{'type': 'text', 'text': '...'}]).
    Session-lines och prompt-bygge kräver ren text — annars sparas
    dict-formatet som strängrepresentation i DB (bugg: content ser ut
    som "[{'type': 'text', 'text': '...'}]").
    """
    if isinstance(content, list):
        texts = [
            c.get('text', '') for c in content if c.get('type') == 'text'
        ]
        return '\n'.join(texts)
    return content or ''

# ---------------------------------------------------------------------------
# SSE Controller
# ---------------------------------------------------------------------------


class AIStreamController(http.Controller):
    """Server-Sent Events endpoint for streaming AI responses."""

    @http.route('/ai/ping', type='http', auth='none', cors='*', sitemap=False)
    def ping(self):
        return request.make_response('pong', [('Content-Type', 'text/plain')])

    @http.route('/ai/stream', type='http', auth='public', cors='*', sitemap=False)
    def stream(self, coworker_id=None, prompt=None, session_id=None, **kw):
        """Stream AI response via SSE. Supports session persistence."""
        if not prompt:
            return Response(
                json.dumps({"error": "Missing prompt parameter"}),
                status=400,
                content_type='application/json',
            )

        # Resolve quest configuration — frontend skickar quest_id (alias för coworker_id)
        from odoo.addons.ai_agent_core.core.provider import get_default_model_name
        model = get_default_model_name()
        system_prompt = ""
        quest = None
        if not coworker_id:
            coworker_id = kw.get('quest_id')

        if coworker_id:
            try:
                quest = request.env['ai.coworker'].sudo().browse(int(coworker_id))
                if quest.exists():
                    # Access check
                    if request.env.user and not _quest_is_accessible(quest, request.env.user):
                        return Response(
                            json.dumps({"error": "Quest not accessible"}),
                            status=403,
                            content_type='application/json',
                        )
                    # Budgetcheck (budget-hard-cap D4): web-UI visar budget slut
                    quest._unlock_budget_activities()
                    if quest.budget_exhausted:
                        quest.check_cap()
                        return Response(
                            json.dumps({
                                "error": "budget_exhausted",
                                "message": "Budget slut: AI-medarbetaren har nått "
                                           "månadstaket. Höj taket i inställningarna "
                                           "eller vänta till nästa månad.",
                            }),
                            status=402,
                            content_type='application/json',
                        )
                    if quest.description:
                        system_prompt = quest.description
                    # Inject skill context for Skill Builder (sudo needed — public users can't read ai.skill)
                    context_skill = kw.get('context_skill')
                    if context_skill:
                        skill = request.env['ai.skill'].sudo().browse(int(context_skill))
                        if skill.exists():
                            skill_context = (
                                f"\n\n## SKILL TO IMPROVE (always remember this context)\n"
                                f"Name: {skill.name}\n"
                                f"Category: {skill.category or 'general'}\n"
                                f"Triggers: {skill.trigger_keywords or ''}\n"
                                f"Description: {skill.description or ''}\n"
                                f"Recipe:\n{skill.recipe_text or '(empty)'}\n"
                            )
                            system_prompt = system_prompt + skill_context
                            _logger.info('Injected skill #%s into system prompt for Skill Builder', context_skill)
                    # Inject quest context for Quest Builder — otherwise the
                    # builder doesn't know WHICH quest "Study this quest" means
                    context_quest = kw.get('context_quest')
                    if context_quest:
                        cq = request.env['ai.coworker'].sudo().browse(int(context_quest))
                        if cq.exists():
                            agents_desc = ", ".join(
                                f"{rel.agent_id.name} ({rel.agent_id.model_id.name if rel.agent_id.model_id else '?'})"
                                for rel in cq.agent_ids) or "(inga agenter)"
                            quest_context = (
                                f"\n\n## QUEST TO STUDY/IMPROVE (always remember this context — this is 'the quest' the user refers to)\n"
                                f"Name: {cq.name}\n"
                                f"Status: {cq.status}\n"
                                f"Init type: {cq.init_type}\n"
                                f"Description: {(cq.description or '')[:800]}\n"
                                f"Agents: {agents_desc}\n"
                            )
                            system_prompt = system_prompt + quest_context
                            _logger.info('Injected quest #%s into system prompt for Quest Builder', context_quest)
                    # Get model from quest's agents (ai_agent_core fields)
                    for qa in quest.agent_ids:
                        agent = qa.agent_id
                        if agent.model_id:
                            model = agent.model_id._get_api_name()
                            break
            except Exception:
                pass

        # -- Slash command detection (Hermes-inspired) --
        user_instruction = prompt
        # Pattern: /skill-name rest of prompt
        if prompt and prompt.startswith('/') and '/' not in prompt.split()[0][1:]:
            parts = prompt.split(None, 1)
            skill_name = parts[0][1:]  # Strip leading /
            user_instruction = parts[1] if len(parts) > 1 else ''

            if quest and quest.exists():
                # Look up skill from quest
                available = quest.get_available_skills()
                matched = [s for s in available if s['name'] == skill_name]
                if matched:
                    skill = matched[0]
                    # Inject skill recipe as system context
                    skill_context = (
                        f"[SKILL ACTIVATED: {skill['name']}]\n"
                        f"The user has explicitly invoked this skill with /{skill['name']}.\n"
                        f"Follow the recipe below for this task:\n\n"
                        f"{skill['recipe_text']}\n\n"
                        f"[END SKILL: {skill['name']}]\n\n"
                    )
                    if user_instruction:
                        skill_context += (
                            f"User instruction accompanying the skill invocation:\n"
                            f"{user_instruction}\n"
                        )
                    system_prompt = skill_context + (system_prompt or '')
                    _logger.info(
                        "Slash skill activated: /%s (quest=%s)",
                        skill_name, quest.name,
                    )

        # -- Inject available skills catalog into system prompt --
        if quest and quest.exists() and not (prompt and prompt.startswith('/')):
            skills = quest.get_available_skills()
            if skills:
                skill_lines = ["\n## Available Skills",
                    "(Activate explicitly with /skill-name. Skills also activate "
                    "automatically when user's message matches trigger keywords.)\n"]
                for s in skills:
                    trigger_info = ""
                    if s.get('trigger_keywords'):
                        trigger_info = f" [triggers: {s['trigger_keywords']}]"
                    skill_lines.append(
                        f"- **{s['name']}**: {s.get('description', '')[:200]}{trigger_info}"
                    )
                system_prompt = (system_prompt or '') + '\n'.join(skill_lines)

        # Aktuell användare + minne via gemensam injiceringsfunktion
        # (agent-memory-governance 3.x — D1/D2)
        if quest and quest.exists():
            try:
                inj = quest._build_injection_prompt(
                    user=request.env.user, prompt=prompt)
                if inj:
                    system_prompt = (system_prompt + '\n\n' + inj).strip()
            except Exception as e:
                _logger.warning('Injektion misslyckades: %s', e)

        # Aktuellt datum — LLM:en har kunskaps-cutoff och behöver veta "idag"
        # för tidssensitiv information (priser, release-datum, valuta, …).
        from datetime import date as _date
        system_prompt = (
            f"Today is {_date.today().isoformat()}. "
            "Your knowledge has a cutoff — use web_search/fetch_url for "
            "current, up-to-date information.\n\n"
            + (system_prompt or '')
        ).strip()

        # Load thread history if session_id provided
        session = None
        history_messages = []
        if session_id:
            try:
                session = request.env['ai.coworker.session'].sudo().browse(int(session_id))
                if session.exists():
                    # Inject quest + session memories into system prompt
                    if quest:
                        memories_text = _get_quest_memories(
                            quest, session_id=session.id, query=prompt
                        )
                        if memories_text:
                            system_prompt = (system_prompt + '\n\n' + memories_text).strip()

                    # Load history from session lines
                    lines = session.session_line_ids.sorted('sequence')
                    for line in lines:
                        history_messages.append({
                            'role': line.role,
                            'content': line.content or '',
                        })
                    # Auto-summarize if too many messages
                    if len(lines) > 50:
                        summary = _summarize_history(session, lines)
                        history_messages = [{'role': 'system', 'content': summary}] + history_messages[-20:]

                    # Save user message as session line (T7.4)
                    next_seq = len(lines) + 1
                    request.env['ai.coworker.session.line'].sudo().create({
                        'session_id': session.id,
                        'sequence': next_seq,
                        'role': 'user',
                        'content': prompt,
                    })
                    session.write_date = fields.Datetime.now()

                    # Multi-surface: mirror user message to channel for buzz workspaces
                    if quest and quest.orchestration_mode == 'buzz' and quest.channel_id:
                        try:
                            author = request.env.user.partner_id
                            if not author:
                                author = request.env.ref('base.partner_root')
                            quest.channel_id.sudo().with_context(
                                buzz_web_ui_sync=True
                            ).message_post(
                                body=f'<p>{escape(prompt)}</p>',
                                message_type='comment',
                                subtype_xmlid='mail.mt_comment',
                                author_id=author.id,
                            )
                        except Exception:
                            _logger.warning('Failed to mirror web UI message to channel', exc_info=True)
            except Exception:
                pass

        # -- Hoist recordset reads for the SSE generator --
        # Werkzeug iterates the generator AFTER Odoo has torn down the
        # request context, so `request` and recordsets are unbound inside
        # generate(). Extract everything needed into plain values here.
        gen_is_supervisor = False
        gen_agents = []  # [{'name', 'description', 'model'}]
        # Resolved provider + model for the post-teardown generator
        gen_provider = None
        gen_provider_model = None  # model name string for non-supervisor mode
        # DB identity for the post-teardown cursor (tool execution needs an
        # env, but `request.env` is unbound inside the SSE generator)
        gen_dbname = request.env.cr.dbname
        gen_uid = request.env.uid
        gen_context = dict(request.env.context)
        # Konversationshistorik (session lines) — hoistas som plain values
        gen_history = history_messages
        # Custom tools (ai.tool via coworker.tool_ids) — fångas som plain
        # values och laddas in i _stream() via gen_env. Access-filtreras mot
        # den inloggade användarens grupper (tool-access-groups): LLM:en ser
        # aldrig verktyg vars res.groups användaren saknar.
        gen_coworker_id = quest.id if quest and quest.exists() else None
        # explicit-agent-tools: ENDAST settings-default + explicita verktyg
        # (agent.tool_ids + coworker.tool_ids) — inga interna builtins per
        # default. Fångas som plain values (generatorn körs efter teardown).
        gen_tool_ids = list(
            quest._session_tool_ids(
                access_groups=request.env.user.groups_id.ids)
        ) if quest and quest.exists() else []
        # Användarens grupper för PermissionEngine (defense-in-depth)
        gen_user_group_ids = tuple(request.env.user.groups_id.ids)
        # NATS executor config (tool-executor-nats)
        nats_api_secret = request.env['ir.config_parameter'].sudo().get_param(
            'ai_agent_core.api_secret', '')
        nats_max_retries = int(request.env['ir.config_parameter'].sudo().get_param(
            'pi.nats.max_retries', '3'))
        if quest and quest.exists():
            try:
                gen_is_supervisor = bool(quest.is_supervisor)
                if gen_is_supervisor and len(quest.agent_ids) > 1:
                    for agent_rel in quest.agent_ids:
                        agent = agent_rel.agent_id
                        gen_agents.append({
                            'name': agent.name,
                            'description': agent.get_agent_name(),
                            'model': (agent.model_id._get_api_name() if agent.model_id else model),
                        })
                # Resolve provider from quest's agent chain.
                # Coworker = skal, agent = hjärna: en coworker utan agent
                # (eller agent utan modell) ger ingen provider → chatten
                # kraschade med 'NoneType' ... chat_stream. Auto-skapa
                # default-agenten (samma som _check_quest_error/_ensure_agent
                # gör i andra kanaler) och fallbacka till default-providern.
                from odoo.addons.ai_agent_core.core.provider import (
                    ProviderFactory, get_default_provider)
                if not quest.agent_ids:
                    try:
                        quest._ensure_agent()
                    except Exception:
                        _logger.exception('Failed to auto-create default agent')
                provider_instance, provider_model = ProviderFactory.from_coworker(quest)
                if provider_instance:
                    gen_provider = provider_instance
                    if provider_model:
                        gen_provider_model = provider_model._get_api_name()
                else:
                    # Fallback till default-provider MÅSTE fångas här (innan
                    # teardown). get_default_provider() inuti generatorn
                    # returnerar alltid (None, None) — `request` är borta
                    # då, vilket gav 'NoneType' ... chat_stream.
                    try:
                        default_provider, default_model = get_default_provider()
                        if default_provider:
                            gen_provider = default_provider
                            if default_model and not gen_provider_model:
                                gen_provider_model = default_model._get_api_name()
                    except Exception:
                        _logger.exception('Failed to resolve default provider')
            except Exception:
                _logger.exception('Failed to extract quest data for streaming')

        def generate():
            """SSE event generator — runs async loop in sync context.

            NOTE: runs after request teardown — no `request`, no recordsets,
            no DB cursor. Only plain values captured above.
            """
            full_response = []
            try:
                _logger.info("SSE stream starting — prompt: %s...", prompt[:50])
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    async def _stream(gen_env):
                        import uuid as _uuid_mod
                        from odoo.addons.ai_agent_core.core.tools import ToolRegistry, ai_tool_records_to_tools
                        from odoo.addons.ai_agent_core.core.loop import StreamingAgentLoop, AgentConfig
                        from odoo.addons.ai_agent_core.core.supervisor import StreamingSupervisorLoop, SupervisorConfig, SpecialistAgent
                        from odoo.addons.ai_agent_core.core.interrupt import WebUIInterruptHandler
                        from odoo.addons.ai_agent_core.core.provider import Message, Role

                        # Konversationshistorik → Message-objekt (kontext mellan varv)
                        # TOOL-rader hoppas över: de saknar assistant-tool_calls-
                        # strukturen vid replay och ger 400 från providern.
                        _ROLE_MAP = {
                            'user': Role.USER, 'assistant': Role.ASSISTANT,
                            'system': Role.SYSTEM,
                        }
                        history = []
                        for item in (gen_history or []):
                            content = item.get('content', '') or ''
                            if not content:
                                continue
                            role = item.get('role')
                            if role == 'tool':
                                continue  # implementeringsdetalj, ej konversation
                            history.append(Message(
                                role=_ROLE_MAP.get(role, Role.USER),
                                content=content,
                            ))
                        # HITL: registrera WebUI-interrupt-handler för denna
                        # stream så att godkännanden (odoo_call_method,
                        # odoo_write, odoo_unlink …) når användaren i chatten.
                        session_uuid = str(_uuid_mod.uuid4())
                        handler = WebUIInterruptHandler(session_uuid, env=gen_env)
                        _register_webui_handler(session_uuid, handler)
                        yield f"data: {json.dumps({'type': 'session', 'session_uuid': session_uuid})}\n\n"

                        provider = gen_provider
                        if provider is None:
                            # Defense-in-depth: fallbacken fångades i
                            # request-kontexten ovan. Här inne (efter
                            # teardown) finns inget `request` att fallbacka
                            # med — ge ett tydligt fel istället för
                            # AttributeError.
                            _logger.error(
                                'No AI provider resolvable for quest %s',
                                gen_coworker_id)
                            yield ("data: " + json.dumps({
                                'type': 'error',
                                'message': ('Ingen AI-leverantör konfigurerad '
                                            'för denna medarbetare. Kontrollera '
                                            'agentens modell eller '
                                            'ai_agent_core.default_model_id.'),
                            }) + "\n\n")
                            return
                        # explicit-agent-tools: ENDAST settings-default +
                        # explicita verktyg (gen_tool_ids). Inga builtins.
                        tools = ToolRegistry()
                        if gen_tool_ids:
                            # sudo-läsning: offentlig chatt-användare saknar
                            # ai.tool-access; gruppfiltreringen av gen_tool_ids
                            # skedde redan (access-gating).
                            tool_recs = gen_env['ai.tool'].sudo().browse(
                                gen_tool_ids)
                            if tool_recs:
                                tools.register_many(ai_tool_records_to_tools(
                                    tool_recs, gen_env))

                        def _make_loop(**kw):
                            """Bygg StreamingAgentLoop med interrupt-handler."""
                            cfg = dict(
                                provider=provider, tools=tools,
                                interrupt_handler=handler,
                            )
                            cfg.update(kw)
                            return StreamingAgentLoop(**cfg)

                        if gen_is_supervisor and len(gen_agents) > 1:
                            # Build supervisor with streaming
                            specialists = []
                            for a in gen_agents:
                                specialists.append(SpecialistAgent(
                                    name=a['name'],
                                    description=a['description'],
                                    loop=_make_loop(
                                        config=AgentConfig(
                                            model=a['model'],
                                            system_prompt=system_prompt,
                                            max_rounds=10,
                                            # Långa tool-resultat (YouTube-transkript ≈ 24k tecken) får plats
                                            max_tool_result_chars=40000,
                                            nats_api_secret=nats_api_secret,
                                            nats_max_retries=nats_max_retries,
                                            user_group_ids=gen_user_group_ids,
                                        ),
                                    ),
                                ))
                            loop_obj = StreamingSupervisorLoop(
                                router_provider=provider, agents=specialists,
                                config=SupervisorConfig(router_model=model),
                            )
                        else:
                            loop_obj = _make_loop(
                                config=AgentConfig(
                                    model=model,
                                    system_prompt=system_prompt,
                                    max_rounds=10,
                                    # Långa tool-resultat (YouTube-transkript ≈ 24k tecken) får plats
                                    max_tool_result_chars=40000,
                                    nats_api_secret=nats_api_secret,
                                    nats_max_retries=nats_max_retries,
                                    user_group_ids=gen_user_group_ids,
                                ),
                            )
                        state['loop_obj'] = loop_obj

                        async for event in loop_obj.run_stream(prompt, history=history):
                            # Vidarebefordra pending HITL-interrupts som SSE
                            pending = handler.get_pending()
                            if pending:
                                yield (f"data: {json.dumps({
                                    'type': pending['type'],
                                    **pending['data'],
                                    'session_uuid': session_uuid,
                                })}\n\n")
                            data = {"type": event.type}
                            if event.type == "token":
                                data["token"] = event.token
                                full_response.append(event.token)
                            elif event.type == "thinking":
                                # Modellens reasoning — sänd som egen SSE-händelse
                                # så web-UI:t kan visa en hopfällbar tänksektion.
                                data["token"] = event.token
                            elif event.type in ("debug", "source", "tool_progress"):
                                data["token"] = event.token
                            elif event.type == "tool_call_start":
                                if event.tool_call:
                                    data["tool_call"] = {
                                        "id": event.tool_call.id,
                                        "name": event.tool_call.name,
                                    }
                            elif event.type in ("done", "error"):
                                data["finish_reason"] = event.finish_reason
                                if event.type == "done":
                                    # Verklig token-usage från loopen —
                                    # frontend bokför per meddelande.
                                    data["input_tokens"] = getattr(
                                        event, 'input_tokens', 0) or 0
                                    data["output_tokens"] = getattr(
                                        event, 'output_tokens', 0) or 0
                            yield f"data: {json.dumps(data)}\n\n"
                        pending = handler.get_pending()
                        if pending:
                            yield (f"data: {json.dumps({
                                'type': pending['type'],
                                **pending['data'],
                                'session_uuid': session_uuid,
                            })}\n\n")
                        _unregister_webui_handler(session_uuid)

                    # Fresh cursor + env for the post-teardown phase:
                    # tool handlers run ORM calls while streaming.
                    from odoo import api as _api, registry as _registry
                    state = {'loop_obj': None}
                    with _registry(gen_dbname).cursor() as gen_cr:
                        gen_env = _api.Environment(gen_cr, gen_uid, gen_context)
                        results = loop.run_until_complete(_collect(_stream(gen_env)))
                        # Efter streamen: persistera verktygsanrop som
                        # role='tool'-rader (granskningsbar kontext per
                        # meddelande) — loop_obj.tool_history fylls av
                        # _execute_tool under strömningen.
                        try:
                            _persist_stream_tool_lines(
                                gen_env, session_id, state.get('loop_obj'))
                        except Exception:
                            _logger.warning(
                                'persist stream tool lines failed', exc_info=True)
                        gen_cr.commit()
                    for chunk in results:
                        yield chunk
                finally:
                    loop.close()
            except Exception as e:
                _logger.error("SSE stream error: %s", e, exc_info=True)
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return request.make_response(
            generate(),
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'X-Accel-Buffering': 'no',
            }
        )

    @http.route('/ai/chat', type='http', auth='public', sitemap=False)
    def chat_ui(self, **kw):
        """Render standalone AI chat interface with threads, theme, and responsive design."""
        user = request.env.user

        # Load quests that have a web_ui init type (filtered by access).
        # Only ACTIVE coworkers (soft-delete flag) with status='active' —
        # otherwise archived/duplicate coworkers leak into the selector.
        quests = request.env['ai.coworker'].sudo().search(
            [('status', '=', 'active'), ('active', '=', True)],
            order='sequence asc, name asc',
        )
        accessible_quests = []
        for q in quests:
            # Check access via _quest_is_accessible or fallback
            if _quest_is_accessible and user:
                if not _quest_is_accessible(q, user):
                    continue
                accessible_quests.append(q)
            else:
                if q.show_in_chat and not q.group_ids and not q.user_ids:
                    accessible_quests.append(q)

        # Filter to only quests with an active web_ui init type
        web_ui_quests = []
        for q in accessible_quests:
            web_ui_init = q.init_type_ids.filtered(
                lambda it: it.init_type == 'web_ui' and it.enabled
            )
            if web_ui_init:
                web_ui_quests.append(q)

        # Default AI-medarbetare: is_default=True (annars första). Den visas
        # förvald i dropdownen — data-driven via xmlid, ingen hårdkodning.
        default_quest = next(
            (q for q in web_ui_quests if q.is_default),
            web_ui_quests[0] if web_ui_quests else None,
        )

        default_option = ''
        quest_items = ''
        if default_quest:
            default_option = (
                f'<option value="{default_quest.id}" '
                f'data-name="{escape(default_quest.name)}" selected>'
                f'{escape(default_quest.name)}</option>'
            )
        for q in web_ui_quests:
            if default_quest and q.id == default_quest.id:
                continue
            quest_items += (
                f'<option value="{q.id}" data-name="{escape(q.name)}">{escape(q.name)}</option>'
            )

        default_qid = str(default_quest.id) if default_quest else ''
        default_qname = escape(default_quest.name) if default_quest else 'Allmän assistent'

        # Load user's threads (most recent 50)
        thread_items = ''
        if user and user.id:
            sessions = request.env['ai.coworker.session'].sudo().search([
                ('user_id', '=', user.id),
                ('active', '=', True),
            ], order='write_date desc', limit=50)
            for s in sessions:
                name = s.thread_name or (s.name or 'Tråd')
                thread_items += (
                    f'<div class="thread-item" data-id="{s.id}" data-name="{escape(name)}">'
                    f'<span class="thread-icon">📝</span>'
                    f'<span class="thread-name">{escape(name)}</span>'
                    f'<span class="thread-delete" title="Radera">×</span>'
                    f'</div>'
                )

        html = (_CHAT_HTML_v3
                .replace('<!-- DEFAULT_OPTION -->', default_option)
                .replace('<!-- QUEST_OPTIONS -->', quest_items)
                .replace('<!-- WELCOME_TITLE -->', default_qname)
                .replace('<!-- DEFAULT_QUEST_ID -->', default_qid)
                .replace('<!-- DEFAULT_QUEST_NAME -->', default_qname)
                .replace('<!-- THREAD_ITEMS -->', thread_items)
                .replace('<!-- USER_AUTH_FLAG -->',
                         'true' if (user and not user._is_public()) else 'false'))
        # no-store: chat_template.html is inline JS — a cached page keeps
        # running stale frontend code after deploys (bit us in production)
        return Response(html, headers=[
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Cache-Control', 'no-store, must-revalidate'),
        ])

    # === Skills API (slash commands) ===

    @http.route('/ai/quest/skills', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def quest_skills(self, coworker_id=None, **kw):
        """Return available skills for a quest as JSON.

        Used by the web chat UI to populate the slash-command
        dropdown. Returns skills from the quest's agents,
        identity, and quest-specific copies.
        """
        skills = []
        if coworker_id:
            try:
                quest = request.env['ai.coworker'].sudo().browse(int(coworker_id))
                if quest.exists():
                    available = quest.get_available_skills()
                    for s in available:
                        skills.append({
                            'name': s['name'],
                            'description': s['description'][:200],
                            'trigger_keywords': s['trigger_keywords'],
                            'category': s['category'],
                        })
            except Exception as e:
                _logger.warning('quest_skills error: %s', e)

        return Response(
            json.dumps({'skills': skills}),
            content_type='application/json',
        )

    @http.route('/ai/skill/<int:skill_id>', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def skill_get(self, skill_id, **kw):
        """Return full skill data as JSON for auto-init context."""
        try:
            skill = request.env['ai.skill'].sudo().browse(skill_id)
            if not skill.exists():
                return Response(json.dumps({'error': 'Skill not found'}),
                              status=404, content_type='application/json')
            return Response(json.dumps({
                'id': skill.id,
                'name': skill.name,
                'description': skill.description or '',
                'category': skill.category or 'general',
                'trigger_keywords': skill.trigger_keywords or '',
                'recipe_text': skill.recipe_text or '',
            }), content_type='application/json')
        except Exception as e:
            return Response(json.dumps({'error': str(e)}),
                          status=500, content_type='application/json')

    # === Model API (quest model selector) ===

    @http.route('/ai/quest/models', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def quest_models(self, coworker_id=None, **kw):
        """Return available models for a quest's agents with is_vision flag.

        Uses ai_agent_core fields only: agent_ids → agent_id.model_id → ai.model.
        """
        models = []
        seen = set()
        if coworker_id:
            try:
                quest = request.env['ai.coworker'].sudo().browse(int(coworker_id))
                if quest.exists():
                    for qa in quest.agent_ids:
                        agent = qa.agent_id
                        if not agent.model_id:
                            continue
                        ai_model = agent.model_id
                        model_name = ai_model.name
                        if ai_model.id in seen:
                            continue
                        seen.add(ai_model.id)
                        models.append({
                            'name': model_name,
                            'display_name': ai_model.display_name or model_name,
                            'is_vision': bool(ai_model.is_vision),
                            'agent_name': agent.name,
                            'model_id': ai_model.id,
                        })
            except Exception as e:
                _logger.warning('quest_models error: %s', e)

        return Response(
            json.dumps({'models': models}),
            content_type='application/json',
        )

    @http.route('/ai/interrupt/poll', type='http', auth='public', cors='*', sitemap=False)
    def interrupt_poll(self, session_uuid=None, **kw):
        """SSE endpoint for interrupt events (needs_input, needs_approval)."""
        if not session_uuid:
            return Response(
                json.dumps({"error": "Missing session_uuid"}),
                status=400, content_type='application/json',
            )

        def generate():
            handler = _get_webui_handler(session_uuid)
            if not handler:
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
                return

            while True:
                pending = handler.get_pending()
                if pending:
                    yield f"data: {json.dumps(pending)}\n\n"
                    return
                time.sleep(0.5)

        return request.make_response(
            generate(),
            headers={
                'Content-Type': 'text/event-stream',
                'Cache-Control': 'no-cache',
            }
        )

    @http.route('/ai/interrupt/respond', type='json', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def interrupt_respond(self, session_uuid=None, response=None, **kw):
        """POST endpoint for human response to interrupt."""
        if not session_uuid or not response:
            return {"error": "Missing session_uuid or response"}

        handler = _get_webui_handler(session_uuid)
        if handler:
            handler.set_response(response)
            return {"status": "ok"}
        return {"error": "No pending interrupt for this session"}

    # === Thread API (web-chat-threads-memory change) ===

    @http.route('/ai/threads', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def thread_list(self, **kw):
        """List user's threads — filtrerat på vald coworker (coworker_id)."""
        user = request.env.user
        if not user or not user.id:
            return Response(json.dumps({"threads": []}), content_type='application/json')
        domain = [
            ('user_id', '=', user.id),
            ('active', '=', True),
        ]
        cw_id = kw.get('coworker_id')
        if cw_id:
            try:
                domain.append(('coworker_id', '=', int(cw_id)))
            except (ValueError, TypeError):
                pass
        sessions = request.env['ai.coworker.session'].sudo().search(
            domain, order='write_date desc', limit=50)
        return Response(json.dumps({
            "threads": [{
                "id": s.id,
                "name": s.thread_name or (s.name or 'Tråd'),
                "coworker_id": s.coworker_id.id if s.coworker_id else None,
                "skill_id": s.skill_id.id if s.skill_id else None,
                "last_activity": str(s.write_date) if s.write_date else None,
                "message_count": s.line_count,
            } for s in sessions]
        }), content_type='application/json')

    @http.route('/ai/threads', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def thread_create(self, **kw):
        """Create a new thread.

        När en NY tråd skapas (web-UI:ets "/new" skapar tråden lazy vid
        första meddelandet) markeras användarens tidigare AKTIVA sessioner
        som done (finish_reason='new_session') — den gamla konversationen är
        "klar" tills den öppnas igen (då förblir den done; återupptagning
        sker via selectThread → thread_get, inte via status).
        """
        user = request.env.user
        # Parse JSON body for type='http' POST
        body = json.loads(request.httprequest.data or '{}')
        name = body.get('name', 'Ny tråd')
        # Clean name: remove newlines, collapse spaces, trim, limit length
        name = ' '.join(str(name).split())[:50]
        coworker_id = body.get('coworker_id') or body.get('quest_id')
        skill_id = body.get('skill_id')
        # Builder context: the auto-init prompt ("Study this quest…") makes a
        # useless thread name — name the thread after the subject quest instead
        context_quest = body.get('context_quest')
        if context_quest:
            try:
                cq = request.env['ai.coworker'].sudo().browse(int(context_quest))
                if cq.exists():
                    name = cq.name[:50]
            except (ValueError, TypeError):
                pass
        vals = {
            'name': name,
            'user_id': user.id if user.id else None,
            'thread_name': name,
            'status': 'active',
        }
        if coworker_id:
            vals['coworker_id'] = int(coworker_id)
        if skill_id:
            vals['skill_id'] = int(skill_id)
        session = request.env['ai.coworker.session'].sudo().create(vals)

        # /new-semantik: stäng användarens övriga aktiva sessioner.
        if user and user.id:
            try:
                request.env['ai.coworker.session'].sudo().search([
                    ('user_id', '=', user.id),
                    ('status', '=', 'active'),
                    ('id', '!=', session.id),
                ]).write({
                    'status': 'done',
                    'finish_reason': 'new_session',
                    'end_date': fields.Datetime.now(),
                })
            except Exception:
                _logger.warning('thread_create: close previous sessions failed',
                                exc_info=True)

        return Response(json.dumps({"id": session.id, "name": session.thread_name}),
                       content_type='application/json')

    @http.route('/ai/threads/<int:thread_id>/close', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def thread_close(self, thread_id, **kw):
        """Stäng en tråd (anropas av web-UI:et när användaren klickar /new).

        Markerar sessionen done med finish_reason='closed' så att statusen
        speglar att konversationen är avslutad. Idempotent: en redan done/
        error-session rörs inte.
        """
        session = request.env['ai.coworker.session'].sudo().browse(thread_id)
        if session.exists() and session.status == 'active':
            session.write({
                'status': 'done',
                'finish_reason': 'closed',
                'end_date': fields.Datetime.now(),
            })
        return Response(json.dumps({"status": "ok"}), content_type='application/json')

    @http.route('/ai/threads/<int:thread_id>', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def thread_get(self, thread_id, **kw):
        """Get thread with messages."""
        session = request.env['ai.coworker.session'].sudo().browse(thread_id)
        if not session.exists():
            return Response(json.dumps({"error": "Thread not found"}), content_type='application/json', status=404)
        lines = session.session_line_ids.sorted('sequence')
        # Konversationsrader (user/assistant/system) visas i UI:t;
        # tool-rader är granskningsdata och exponeras separat som tool_lines
        # så de inte renderas som AI-meddelanden i trådvyn.
        conv_lines = [l for l in lines if l.role != 'tool']
        tool_lines = [l for l in lines if l.role == 'tool']
        return Response(json.dumps({
            "id": session.id,
            "name": session.thread_name or (session.name or ''),
            "coworker_id": session.coworker_id.id if session.coworker_id else None,
            "coworker_name": session.coworker_id.name if session.coworker_id else None,
            "status": session.status,
            "finish_reason": session.finish_reason,
            "messages": [{
                "role": l.role,
                "content": l.content or '',
                "tool_name": l.tool_name,
                "debug_info": l.debug_info or '',
                "tool_calls": l.tool_calls or '',
                "model_real": l.model_real or '',
                "token_input": l.token_input or 0,
                "token_output": l.token_output or 0,
                "token_sys": l.token_sys or 0,
            } for l in conv_lines],
            "tool_lines": [{
                "tool_name": l.tool_name or '',
                "content": (l.content or '')[:500],
                "token_sys": l.token_sys or 0,
            } for l in tool_lines],
        }), content_type='application/json')

    @http.route('/ai/threads/<int:thread_id>', type='http', auth='public',
                methods=['PUT'], csrf=False, sitemap=False)
    def thread_rename(self, thread_id, **kw):
        """Rename a thread."""
        body = json.loads(request.httprequest.data or '{}')
        name = body.get('name', '')
        session = request.env['ai.coworker.session'].sudo().browse(thread_id)
        if session.exists():
            session.thread_name = name[:200]
            session.name = name[:200]
        return Response(json.dumps({"status": "ok"}), content_type='application/json')

    @http.route('/ai/threads/<int:thread_id>', type='http', auth='public',
                methods=['DELETE'], csrf=False, sitemap=False)
    def thread_delete(self, thread_id, **kw):
        """Delete a thread (soft-delete by setting active=False)."""
        session = request.env['ai.coworker.session'].sudo().browse(thread_id)
        if session.exists():
            session.active = False
        return Response(json.dumps({"status": "ok"}), content_type='application/json')

    @http.route('/ai/threads/<int:thread_id>/respond', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def thread_save_response(self, thread_id, **kw):
        """Save an assistant response to a thread (called by frontend after streaming).

        Accepts token tracking fields: token_input, token_output, model_real
        (riktig usage från SSE done-händelsen, annars frontend-estimat).
        Bokföring per meddelande:
          - user-raden (senaste) får requestens input-tokens
          - assistant-raden får output-tokens + debug/källor/tool_calls
        Detta gör att token_sys per rad = (input|output) × sys_multiplier och
        session.token_sys = Σ rader ≈ (input+output) × multiplier (som förr).
        """
        body = json.loads(request.httprequest.data or '{}')
        content = body.get('content', '')
        role = body.get('role', 'assistant')
        model_real = body.get('model_real', '')
        token_input = body.get('token_input', 0)
        token_output = body.get('token_output', 0)
        debug_info = body.get('debug', '')
        sources = body.get('sources') or []
        tool_calls = body.get('tool_calls') or ''
        if not content.strip():
            return Response(json.dumps({"status": "ok"}), content_type='application/json')
        session = request.env['ai.coworker.session'].sudo().browse(thread_id)
        quest = session.coworker_id if session else None
        if session.exists():
            next_seq = len(session.session_line_ids) + 1

            # Resolve sys_multiplier from ai.model if model_real is provided.
            # Kanal-medvetet via _resolve_from_real (record-id/coworker-agenter).
            sys_mult = 1.0
            if model_real:
                ai_model = request.env['ai.model']._resolve_from_real(
                    model_real, quest)
                if ai_model:
                    sys_mult = ai_model.sys_multiplier

            # tool_calls: lista (frontend) eller redan serialiserad sträng.
            tool_calls_json = (
                tool_calls if isinstance(tool_calls, str) else json.dumps(
                    tool_calls, ensure_ascii=False))

            request.env['ai.coworker.session.line'].sudo().create({
                'session_id': session.id,
                'sequence': next_seq,
                'role': role,
                'content': content,
                'debug_info': debug_info,
                'source_urls': '\n'.join(
                    str(s) for s in sources if str(s).startswith('http')),
                'tool_calls': tool_calls_json,
                # Assistant-raden bokför output; input ligger på user-raden.
                'token_input': 0,
                'token_output': token_output,
                'model_real': model_real,
                'sys_multiplier': sys_mult,
            })

            # Senaste user-raden får requestens input-tokens (riktig usage
            # eller estimat) så varje meddelande visar sin prompt-kostnad.
            user_lines = session.session_line_ids.filtered(
                lambda l: l.role == 'user').sorted('sequence', reverse=True)
            if user_lines:
                user_lines[0].write({
                    'token_input': token_input,
                    'token_output': 0,
                    'sys_multiplier': sys_mult,
                })

            # Also update session totals
            session.token_input += token_input
            session.token_output += token_output
            session.write_date = fields.Datetime.now()

            # Increment quest all-time totals
            if session.coworker_id:
                quest = session.coworker_id
                quest.total_input_tokens += token_input
                quest.total_output_tokens += token_output
                quest.total_sys_tokens += int((token_input + token_output) * sys_mult)
                # Trigger cap check
                if quest.monthly_cap_mtokens:
                    quest.check_cap()

                # Hermes-lärande (agent-memory-governance 4.x): LLM-reflektion
                # i bakgrunden när medarbetaren är aktivt lärande. Tråden
                # skapar EGEN DB-cursor/env (requestens stängs efter svar).
                if quest and quest.learning == 'active' and role == 'assistant':
                    try:
                        quest._maybe_learn_async(session.id)
                    except Exception:
                        pass

                # Proactive company mission evolution (Hole 9)
                try:
                    if quest.use_company_info and role == 'assistant' and len(session.session_line_ids) >= 4:
                        company = request.env.user.company_id
                        if company.company_mission and request.env.user.has_group('base.group_system'):
                            # Check thresholds
                            config = request.env['ir.config_parameter'].sudo()
                            interval = int(config.get_param('company.mission_review_interval_days', '30'))
                            confidence_threshold = float(config.get_param('company.mission_confidence_threshold', '0.7'))
                            last_review = company.company_mission_last_review or company.company_values_last_review
                            if not last_review or (fields.Datetime.now() - last_review).days >= interval:
                                # Thread the detection — don't block the response
                                import threading
                                threading.Thread(
                                    target=_detect_and_suggest_mission,
                                    args=(session.id, content, company.id, confidence_threshold),
                                    daemon=True,
                                ).start()
                except Exception:
                    pass  # Never fail the response over mission detection

            _logger.info("Saved response to session %s: %d in/%d out tokens, model=%s",
                        thread_id, token_input, token_output, model_real or 'unknown')

            # Multi-surface: mirror assistant response to channel for buzz workspaces
            if quest and quest.orchestration_mode == 'buzz' and quest.channel_id and role == 'assistant':
                try:
                    # Post as the first agent, or fallback to root
                    agent = quest.agent_ids[:1].agent_id if quest.agent_ids else None
                    author = agent.partner_id if agent and agent.partner_id else request.env.ref('base.partner_root')
                    quest.channel_id.sudo().with_context(
                        buzz_web_ui_sync=True
                    ).message_post(
                        body=f'<p>{escape(content)}</p>',
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment',
                        author_id=author.id,
                    )
                except Exception:
                    _logger.warning('Failed to mirror assistant response to channel', exc_info=True)

        return Response(json.dumps({"status": "ok"}), content_type='application/json')

    @http.route('/ai/thread/search', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def thread_search(self, q='', **kw):
        """Search threads by message content."""
        user = request.env.user
        if not q or len(q) < 2 or not user or not user.id:
            return Response(json.dumps({"threads": []}), content_type='application/json')
        lines = request.env['ai.coworker.session.line'].sudo().search([
            ('content', 'ilike', q),
            ('session_id.user_id', '=', user.id),
            ('session_id.active', '=', True),
        ], limit=50)
        thread_ids = list(set(lines.mapped('session_id.id')))
        sessions = request.env['ai.coworker.session'].sudo().browse(thread_ids)
        return Response(json.dumps({
            "threads": [{
                "id": s.id,
                "name": s.thread_name or (s.name or 'Tråd'),
                "last_activity": str(s.write_date) if s.write_date else None,
            } for s in sessions]
        }), content_type='application/json')

    # === Powerbox API (slash command) ===

    @http.route('/ai/powerbox/lookup', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def powerbox_lookup(self, model=None, res_id=None, **kw):
        """Return powerbox quests available for a given model/record.

        Called by the frontend slash-command widget when user types '/'.
        Returns quests filtered by the record's model.
        """
        if not model:
            return Response(json.dumps({"quests": []}),
                          content_type='application/json')

        quests = request.env['ai.coworker'].sudo().search([
            ('status', '=', 'active'),
            ('active', '=', True),
            ('init_type_ids.init_type', '=', 'powerbox'),
            ('init_type_ids.enabled', '=', True),
            '|',
            ('model_ids.model', '=', model),
            ('model_ids', '=', False),  # No model restriction = available on all
        ], order='sequence asc, name asc')

        return Response(json.dumps({
            "quests": [{
                "id": q.id,
                "name": q.name,
                "sub_description": q.sub_description or '',
                "icon": q._POWERBOX_SVG if hasattr(q, '_POWERBOX_SVG') else '',
                "color": q.color,
            } for q in quests]
        }), content_type='application/json')

    @http.route('/ai/powerbox/run', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def powerbox_run(self, **kw):
        """Run a powerbox quest with text content from a field.

        POST body: {coworker_id, text, model, res_id}
        Returns AI-processed text.
        """
        body = json.loads(request.httprequest.data or '{}')
        coworker_id = body.get('coworker_id')
        text = body.get('text', '').strip()
        model = body.get('model', '')
        res_id = body.get('res_id')

        if not coworker_id or not text:
            return Response(json.dumps({"error": "Missing coworker_id or text"}),
                          content_type='application/json', status=400)

        quest = request.env['ai.coworker'].sudo().browse(int(coworker_id))
        if not quest.exists():
            return Response(json.dumps({"error": "Quest not found"}),
                          content_type='application/json', status=404)
        if 'powerbox' not in quest.init_type_ids.filtered('enabled').mapped('init_type'):
            return Response(json.dumps({"error": "Quest not configured as powerbox"}),
                          content_type='application/json', status=404)

        try:
            result = quest.powerbox(
                prompt=text,
                res_model=model,
                res_id=int(res_id) if res_id else None,
            )
            return Response(json.dumps({
                "status": "ok",
                "result": result,
                "quest_name": quest.name,
            }), content_type='application/json')
        except Exception as e:
            _logger.error('Powerbox run error: %s', e)
            return Response(json.dumps({
                "error": str(e),
            }), content_type='application/json', status=500)

    # === Improvement & Upload ===

    @http.route('/ai/learn', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def learn_command(self, **kw):
        """/learn command — update personal companion identity (Hole 3)."""
        user = request.env.user
        body = json.loads(request.httprequest.data or '{}')
        learning = body.get('learning', '').strip()
        coworker_id = body.get('coworker_id')

        if not learning:
            return Response(json.dumps({"error": "Empty learning"}),
                          content_type='application/json', status=400)

        # Find the quest (personal companion or specified)
        quest = None
        if coworker_id:
            quest = request.env['ai.coworker'].sudo().browse(int(coworker_id))
        elif user.personal_coworker_id:
            quest = user.personal_coworker_id

        if not quest or not quest.exists():
            return Response(json.dumps({
                "error": "No personal companion found. Enable it in Settings first."
            }), content_type='application/json', status=404)

        identity = quest.identity_id
        if not identity:
            return Response(json.dumps({
                "error": "Quest has no identity to update"
            }), content_type='application/json', status=400)

        # Determine what to update based on learning content
        learning_lower = learning.lower()
        if any(kw in learning_lower for kw in ('föredrar', 'prefer', 'gillar', 'like', 'korta svar', 'short answer', 'stil', 'style')):
            identity.style = (identity.style or '') + f'\n- {learning}'
            field_updated = 'style'
        elif any(kw in learning_lower for kw in ('jobbar med', 'work with', 'arbetar', 'fokus', 'focus', 'domain')):
            identity.user_model = (identity.user_model or '') + f'\n- {learning}'
            field_updated = 'user_model'
        else:
            identity.user_model = (identity.user_model or '') + f'\n- {learning}'
            field_updated = 'user_model'

        _logger.info('/learn: updated %s.%s for quest %s: %s',
                    identity.name, field_updated, quest.name, learning[:80])

        return Response(json.dumps({
            "status": "ok",
            "message": f"Jag har noterat: '{learning[:100]}' (uppdaterade {field_updated}).",
            "field": field_updated,
        }), content_type='application/json')

    # === Session document API ===

    @http.route('/ai/session/<int:session_id>/documents', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def session_documents(self, session_id, **kw):
        """List uploaded documents for a session."""
        session = request.env['ai.coworker.session'].sudo().browse(session_id)
        if not session.exists():
            return Response(json.dumps({"documents": []}),
                          content_type='application/json')

        memories = request.env['ai.memory'].sudo().search([
            ('session_id', '=', session.id),
            ('archived', '=', False),
        ])

        return Response(json.dumps({
            "documents": [{
                "id": m.id,
                "name": m.name or 'Dokument',
                "content_preview": (m.content or '')[:200],
                "memory_type": m.memory_type,
                "tags": m.tags or '',
                "can_remove": True,
            } for m in memories]
        }), content_type='application/json')

    @http.route('/ai/memory/<int:memory_id>/archive', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def memory_archive(self, memory_id, **kw):
        """Archive a memory (soft delete)."""
        memory = request.env['ai.memory'].sudo().browse(memory_id)
        if memory.exists():
            memory.archived = True
        return Response(json.dumps({"status": "ok"}),
                      content_type='application/json')

    @http.route('/ai/improve', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def improve_quest(self, **kw):
        """Förbättra-kommando: uppdatera quest med feedback."""
        user = request.env.user
        body = json.loads(request.httprequest.data or '{}')
        coworker_id = body.get('coworker_id')
        guidance_text = body.get('guidance', '')
        if not guidance_text.strip():
            return Response(json.dumps({"error": "Tom förbättringstext"}),
                          content_type='application/json', status=400)
        quest = request.env['ai.coworker'].sudo().browse(int(coworker_id)) if coworker_id else None
        if not quest or not quest.exists():
            return Response(json.dumps({"error": "Quest ej hittad"}),
                          content_type='application/json', status=404)
        is_admin = user.has_group('base.group_system')
        is_owner = quest.user_id and quest.user_id.id == user.id
        if not (is_admin or is_owner):
            return Response(json.dumps({"error": "Saknar rättighet"}),
                          content_type='application/json', status=403)
        memory = request.env['ai.memory'].sudo().create({
            'name': f'Forbattring: {guidance_text[:80]}',
            'content': guidance_text,
            'coworker_id': quest.id,
            'category': 'feedback',
            'importance': 'high',
        })
        if quest.identity_id:
            existing = quest.identity_id.user_model or ''
            new_entry = f"\n- Forbattring ({fields.Datetime.now()}): {guidance_text[:200]}"
            quest.identity_id.user_model = (existing + new_entry)[:4000]
        _logger.info("Improvement on quest %s: %s", quest.name, guidance_text[:100])
        return Response(json.dumps({
            "status": "ok", "memory_id": memory.id,
            "message": f"Forbattring sparad for '{quest.name}'.",
        }), content_type='application/json')

    @http.route('/ai/upload', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def upload_document(self, **kw):
        """Ladda upp dokument → RAG-minne (text eller FAISS).

        Accepts optional session_id to bind memory to a specific session.
        Stores the original file as ir.attachment linked to the session.
        """
        coworker_id = kw.get('coworker_id')
        session_id = kw.get('session_id')
        memory_type = kw.get('memory_type', 'text')
        file_obj = request.httprequest.files.get('file')
        if not file_obj:
            return Response(json.dumps({"error": "Ingen fil"}),
                          content_type='application/json', status=400)
        filename = file_obj.filename
        content = file_obj.read()
        text = _extract_text(filename, content)
        if not text or not text.strip():
            return Response(json.dumps({"error": "Kunde ej extrahera text"}),
                          content_type='application/json', status=400)
        quest = request.env['ai.coworker'].sudo().browse(int(coworker_id)) if coworker_id else None
        session = request.env['ai.coworker.session'].sudo().browse(int(session_id)) if session_id else None

        # Store original file as ir.attachment linked to session
        attachment = None
        if session:
            attachment = request.env['ir.attachment'].sudo().create({
                'name': filename,
                'datas': content,
                'res_model': 'ai.coworker.session',
                'res_id': session.id,
                'mimetype': file_obj.content_type or 'application/octet-stream',
            })

        if memory_type == 'faiss':
            # Create FAISS memory from uploaded document
            try:
                from langchain_core.documents import Document
                doc = Document(page_content=text, metadata={'source': filename})
                memory = request.env['ai.memory'].sudo().create({
                    'name': f'FAISS: {filename}',
                    'content': text[:2000],
                    'coworker_id': quest.id if quest else None,
                    'session_id': session.id if session else None,
                    'agent_id': quest.agent_ids[0].agent_id.id if quest and quest.agent_ids else None,
                    'category': 'fact',
                    'importance': 'medium',
                    'memory_type': 'faiss',
                    'tags': f'uploaded,faiss,{filename}',
                })
                chunk_count = memory.create_vector([doc])
                return Response(json.dumps({
                    'status': 'ok', 'filename': filename,
                    'chars': len(text), 'chunks': chunk_count,
                    'memory_id': memory.id,
                    'attachment_id': attachment.id if attachment else None,
                    'message': f"'{filename}' indexerat som FAISS ({chunk_count} chunks).",
                }), content_type='application/json')
            except Exception as e:
                _logger.warning('FAISS upload failed, falling back to text: %s', e)
                # Fall through to text mode

        # Plain text upload (existing behavior)
        chunks = _chunk_text(text, 2000)
        memories = []
        for i, chunk in enumerate(chunks):
            m = request.env['ai.memory'].sudo().create({
                'name': f'{filename} (del {i+1})' if len(chunks) > 1 else filename,
                'content': chunk,
                'coworker_id': quest.id if quest else None,
                'session_id': session.id if session else None,
                'category': 'fact',
                'importance': 'medium',
                'tags': f'uploaded,{filename}',
            })
            memories.append(m.id)
        _logger.info("Upload: %s (%d chars, %d chunks)", filename, len(text), len(chunks))
        return Response(json.dumps({
            "status": "ok", "filename": filename,
            "chars": len(text), "chunks": len(chunks),
            "memory_ids": memories,
            "message": f"'{filename}' uppladdat ({len(chunks)} minnesposter).",
        }), content_type='application/json')


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

_webui_handlers: dict[str, 'WebUIInterruptHandler'] = {}

def _get_webui_handler(session_uuid: str):
    return _webui_handlers.get(session_uuid)

def _register_webui_handler(session_uuid: str, handler):
    _webui_handlers[session_uuid] = handler

def _unregister_webui_handler(session_uuid: str):
    _webui_handlers.pop(session_uuid, None)


def _get_quest_memories(quest, session_id=None, query=None) -> str:
    """Get consolidated + FAISS + session memories for quest's system prompt.

    Args:
        quest: ai.coworker record
        session_id: Optional session ID for session-level memories
        query: Optional search query for FAISS vector search
    """
    parts = []
    try:
        # 1. Consolidated text memories
        memories = request.env['ai.memory'].sudo().search([
            ('quest_id', '=', quest.id),
            ('consolidated', '=', True),
            ('archived', '=', False),
        ], limit=20)
        if memories:
            items = [f"- {m.content}" for m in memories]
            parts.append("## Lärt om denna quest\n" + "\n".join(items))
    except Exception:
        pass

    try:
        # 2. Agent-level FAISS memories
        if query:
            agent_memories = request.env['ai.memory'].sudo().search([
                ('agent_id', 'in', quest.agent_ids.agent_id.ids),
                ('archived', '=', False),
                ('memory_type', '=', 'faiss'),
            ])
            for mem in agent_memories:
                chunks = mem.faiss_search(query, k=3)
                if chunks:
                    parts.append("## Agent Knowledge\n" + '\n---\n'.join(chunks[:3]))
    except Exception:
        pass

    try:
        # 3. Session-level memories (uploaded documents)
        if session_id:
            session_memories = request.env['ai.memory'].sudo().search([
                ('session_id', '=', int(session_id)),
                ('archived', '=', False),
                ('memory_type', '=', 'faiss'),
            ])
            for mem in session_memories:
                if query:
                    chunks = mem.faiss_search(query, k=3)
                else:
                    chunks = [mem.content[:500]] if mem.content else []
                if chunks:
                    parts.append("## Uploaded Documents\n" + '\n---\n'.join(chunks[:3]))
    except Exception:
        pass

    return "\n\n".join(parts) if parts else ""


async def _collect(agen):
    """Collect all items from an async generator into a list."""
    result = []
    async for item in agen:
        result.append(item)
    return result


def _persist_stream_tool_lines(env, session_id, loop_obj):
    """Persistera verktygsanrop från en web-chat-stream som tool-rader.

    Körs efter att SSE-generatorn strömmat klart (egen cursor). loop_obj.
    tool_history = [(tool_name, preview), ...] fylls av AgentLoop.
    Idempotent: rader som redan finns för sessionen (samma tool_name)
    hoppas över, så en retry/återkörning inte duplicerar.
    """
    if not session_id or not loop_obj:
        return
    history = getattr(loop_obj, 'tool_history', None) or []
    if not history:
        return
    session = env['ai.coworker.session'].sudo().browse(int(session_id))
    if not session.exists():
        return
    existing = set(session.session_line_ids.filtered(
        lambda l: l.role == 'tool').mapped('tool_name'))
    for i, (t_name, t_preview) in enumerate(history):
        if t_name in existing:
            continue
        tool_cost = 500  # default (ai.tool.sys_token_cost)
        try:
            tool_rec = env['ai.tool'].sudo().search(
                [('name', '=', t_name)], limit=1)
            if tool_rec:
                tool_cost = tool_rec.sys_token_cost
        except Exception:
            pass
        env['ai.coworker.session.line'].sudo().create({
            'session_id': session.id,
            'role': 'tool',
            'tool_name': t_name,
            'content': str(t_preview)[:2000],
            'sequence': 100 + i,
            'token_input': tool_cost,
            'sys_multiplier': 1.0,
        })
    _logger.info('persisted %d tool line(s) for session %s',
                 len(history), session_id)


# ---------------------------------------------------------------------------
# Chat UI HTML template
# ---------------------------------------------------------------------------

import os as _os

def _load_chat_template():
    """Load the chat HTML template from file."""
    _path = _os.path.join(_os.path.dirname(__file__), '..', 'views', 'chat_template.html')
    try:
        with open(_os.path.abspath(_path), 'r', encoding='utf-8') as _f:
            return _f.read()
    except Exception:
        return '<html><body>Error loading template</body></html>'

_CHAT_HTML_v3 = _load_chat_template()


def _extract_text(filename: str, content: bytes) -> str:
    """Extract text from uploaded file. Supports:
    - Plain text: txt, md, csv, py, js, html, xml, json, yml, yaml, rst
    - PDF: via PyPDF2
    - Word: docx (python-docx)
    - Excel: xlsx (openpyxl)
    - PowerPoint: pptx (python-pptx)
    - OpenDocument: odt, ods, odp (ZIP+XML parser)
    - Legacy: doc, xls, ppt, rtf (via LibreOffice if installed)
    """
    ext = filename.lower().split('.')[-1] if '.' in filename else ''

    # Plain text formats
    if ext in ('txt', 'md', 'csv', 'py', 'js', 'html', 'xml', 'json', 'yml', 'yaml', 'rst', 'log', 'ini', 'cfg'):
        try:
            return content.decode('utf-8')
        except UnicodeDecodeError:
            return content.decode('utf-8', errors='replace')

    # PDF
    elif ext == 'pdf':
        try:
            import io, PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(content))
            return '\n'.join(p.extract_text() or '' for p in reader.pages)
        except ImportError:
            return f"[PDF kräver PyPDF2 — {len(content)} bytes]"
        except Exception as e:
            return f"[PDF-fel: {e}]"

    # Word .docx
    elif ext == 'docx':
        try:
            import io, docx
            doc = docx.Document(io.BytesIO(content))
            return '\n'.join(p.text for p in doc.paragraphs if p.text.strip())
        except ImportError:
            return f"[DOCX kräver python-docx — {len(content)} bytes]"
        except Exception as e:
            return f"[DOCX-fel: {e}]"

    # Excel .xlsx
    elif ext == 'xlsx':
        try:
            import io, openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            text = []
            for sheet_name in wb.sheetnames:
                ws = wb[sheet_name]
                text.append(f'=== {sheet_name} ===')
                for row in ws.iter_rows(values_only=True):
                    row_text = ' | '.join(str(c) if c is not None else '' for c in row)
                    if row_text.strip():
                        text.append(row_text)
            return '\n'.join(text)
        except ImportError:
            return f"[XLSX kräver openpyxl — {len(content)} bytes]"
        except Exception as e:
            return f"[XLSX-fel: {e}]"

    # PowerPoint .pptx
    elif ext == 'pptx':
        try:
            import io, pptx
            prs = pptx.Presentation(io.BytesIO(content))
            text = []
            for i, slide in enumerate(prs.slides, 1):
                text.append(f'=== Slide {i} ===')
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            if para.text.strip():
                                text.append(para.text)
            return '\n'.join(text)
        except ImportError:
            return f"[PPTX kräver python-pptx — {len(content)} bytes]"
        except Exception as e:
            return f"[PPTX-fel: {e}]"

    # OpenDocument: odt, ods, odp (ZIP med content.xml)
    elif ext in ('odt', 'ods', 'odp'):
        try:
            import io, zipfile, xml.etree.ElementTree as ET
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                if 'content.xml' in zf.namelist():
                    xml_content = zf.read('content.xml')
                    root = ET.fromstring(xml_content)
                    # All text content (ns-agnostic)
                    text = []
                    for elem in root.iter():
                        if elem.text and elem.text.strip():
                            tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
                            if tag in ('p', 'h', 'title', 'td', 'th', 'li'):
                                text.append(elem.text.strip())
                    return '\n'.join(text) if text else f"[ODF: kunde ej extrahera text]"
                else:
                    return f"[ODF: content.xml saknas i {filename}]"
        except Exception as e:
            return f"[ODF-fel: {e}]"

    # Legacy formats: doc, xls, ppt, rtf → LibreOffice headless
    elif ext in ('doc', 'xls', 'ppt', 'rtf'):
        try:
            import subprocess, tempfile, os
            with tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False) as tmp_in:
                tmp_in.write(content)
                tmp_in_path = tmp_in.name
            tmp_out_path = tmp_in_path + '.txt'
            result = subprocess.run(
                ['libreoffice', '--headless', '--convert-to', 'txt:Text',
                 '--outdir', os.path.dirname(tmp_out_path), tmp_in_path],
                capture_output=True, timeout=30,
            )
            if result.returncode == 0 and os.path.exists(tmp_out_path):
                with open(tmp_out_path, 'r', errors='replace') as f:
                    text = f.read()
                os.unlink(tmp_in_path)
                os.unlink(tmp_out_path)
                return text
            os.unlink(tmp_in_path)
            return f"[LibreOffice misslyckades för {filename}]"
        except FileNotFoundError:
            return f"[LibreOffice ej installerat — {filename} kan ej läsas]"
        except Exception as e:
            return f"[{ext.upper()}-fel: {e}]"

    # Fallback: try UTF-8
    else:
        try:
            return content.decode('utf-8')
        except Exception:
            return f"[Binär fil: {filename} ({len(content)} bytes) — formatet '{ext}' stöds ej]"


def _chunk_text(text: str, max_chars: int = 2000) -> list:
    """Split text into chunks."""
    if len(text) <= max_chars:
        return [text]
    return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]


def _summarize_history(session, lines, max_chars=4000):
    """T7.6: Sammanfatta lång sessionshistorik med LLM (tokenbudget).

    Ersätter den gamla heuristiken (första+senaste, mitt kastad).
    Kör en LLM-sammanfattning över de äldre raderna och sparar
    sammanfattningen även som OKF coworker-koncept (session-summary)
    så att nästa session kan återanvända den.
    """
    if len(lines) <= 50:
        return None

    recent = lines[-20:]
    to_summarize = lines[:-20]
    conversation = '\n'.join(
        f"[{l.role}] {l.content[:400]}"
        for l in to_summarize if l.content
    )[-8000:]  # tokenbudget: begränsa input

    summary = None
    quest = session.coworker_id if session else None
    try:
        import asyncio
        from odoo.addons.ai_agent_core.core.provider import (
            ProviderFactory, get_default_provider, get_default_model_name)
        from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig
        provider, provider_model = ProviderFactory.from_coworker(quest) if quest else (None, None)
        if not provider:
            provider, provider_model = get_default_provider()
        model_name = (provider_model and provider_model._get_api_name()) \
            or get_default_model_name()
        loop = AgentLoop(provider=provider, tools=[], config=AgentConfig(
            model=model_name, max_rounds=1, max_tokens=2048))
        prompt = (
            "Sammanfatta konversationen. Behåll alla nyckelfakta, beslut "
            "och kontext. Var koncis men komplett.\n\n" + conversation)
        result = asyncio.run(loop.run(prompt))
        summary = (result.text or '').strip()[:max_chars]
    except Exception as e:
        _logger.warning('LLM-sammanfattning misslyckades: %s', e)

    if not summary:
        # Fallback: heuristik (första + senaste) — behåller något
        first_msg = lines[0].content[:200] if lines and lines[0].content else 'Start'
        recent_txt = '\n'.join(
            f"[{l.role}] {l.content[:100]}" for l in lines[-5:] if l.content)
        summary = (
            f"[Tidigare konversation ({len(lines)} meddelanden). "
            f"Första: {first_msg}. Senaste: {recent_txt}]")

    # Persist till OKF coworker-scope (session-summary) så nästa session
    # kan återanvända den via coworker-minnesinjektion.
    try:
        if quest and 'ai.okf.concept' in request.env and quest.learning == 'active':
            request.env['ai.okf.concept']._okf_upsert(
                'learning',
                concept_key=f'session.{session.id}.summary',
                summary=summary[:1000],
                title=f'Session {session.id} — sammanfattning',
                source_ref=f'ai.coworker.session,{session.id}',
                attribution=[{
                    'source': f'ai.coworker.session,{session.id}',
                    'role': 'summary',
                }],
                owner_coworker_id=quest.id,
                generated_by='session_summary',
            )
    except Exception as e:
        _logger.warning('Session-summary till OKF misslyckades: %s', e)

    return summary


def _detect_and_suggest_mission(session_id, last_response, company_id, threshold=0.7):
    """Async thread: detect mission gap and suggest update via ai.company.memory."""
    try:
        with api.Environment.manage():
            env = api.Environment(request.env.cr, request.env.uid, request.env.context)
            session = env['ai.coworker.session'].browse(session_id)
            if not session.exists():
                return

            quest = session.coworker_id
            company = env['res.company'].browse(company_id)
            if not company.exists():
                return

            # Build conversation context
            lines = session.session_line_ids.sorted('sequence')
            conversation = '\n'.join(
                f"[{l.role}] {l.content[:300]}"
                for l in lines[-8:]
            )

            prompt = f"""
            Compare the conversation below with this company's mission and values.

            Mission: {company.company_mission or '(not set)'}
            Values: {company.company_values or '(not set)'}

            Conversation:
            {conversation}

            Is there a gap, deepening, or clarification opportunity?
            Return ONLY JSON or null:
            {{"has_opportunity": true/false, "type": "gap|deepening|clarification",
              "reason": "...", "suggested_mission": "...", "suggested_values": "...",
              "field": "mission|values|both", "confidence": 0.0-1.0}}
            """
            try:
                Provider = env['ai.provider']
                response = Provider._generate(
                    model='gpt-4o-mini',
                    messages=[{'role': 'user', 'content': prompt}],
                )
                import json as _json
                result = _json.loads(response)
            except Exception:
                return

            if not result.get('has_opportunity') or result.get('confidence', 0) < threshold:
                return

            # Log the suggestion as a company memory for review
            suggested = []
            if result.get('suggested_mission') and result['suggested_mission'] != company.company_mission:
                suggested.append(f"Mission: {result['suggested_mission']}")
            if result.get('suggested_values') and result['suggested_values'] != company.company_values:
                suggested.append(f"Values: {result['suggested_values']}")

            if suggested:
                env['ai.company.memory'].create({
                    'company_id': company.id,
                    'content': (
                        f"**AI-suggested {result.get('type', 'update')}**\n"
                        f"**Reason**: {result.get('reason', '')}\n"
                        f"**Suggested**: {' | '.join(suggested)}\n"
                        f"**Confidence**: {result.get('confidence', 0)}\n"
                        f"**Session**: {session.name}\n"
                        f"**Date**: {fields.Datetime.now()}"
                    ),
                    'category': 'management',
                    'scope': 'public',
                    'importance': 'medium',
                })
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Callback Controllers — external systems report results back to Odoo
# ---------------------------------------------------------------------------

CALLBACK_SECRET_DEFAULT = 'CHANGE_ME_IN_PILLAR'
CALLBACK_SECRET_PARAM = 'ai_agent_core.api_secret'


def _get_callback_secret():
    """Resolve the shared API secret.

    Fallback chain:
      1. System parameter ``ai_agent_core.api_secret``
         (Settings → Technical → System Parameters)
      2. Environment variable ``AI_AGENT_API_SECRET``
         (injectable via Salt pillar → systemd Environment=)
      3. Hardcoded default (development only — should never ship to prod)
    """
    try:
        param = request.env['ir.config_parameter'].sudo().get_param(
            CALLBACK_SECRET_PARAM)
        if param:
            return param
    except Exception:
        pass
    import os
    return os.environ.get('AI_AGENT_API_SECRET', CALLBACK_SECRET_DEFAULT)


class PICallbackController(http.Controller):
    """Endpoints for Pi workers, Zabbix, and Bifrost to report results."""

    def _check_callback_auth(self):
        """Validate pre-shared token from Authorization header."""
        auth = request.httprequest.headers.get('Authorization', '')
        expected = f'Bearer {_get_callback_secret()}'
        if auth != expected:
            return False
        return True

    @http.route('/pi/callback/<int:task_id>', type='json', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def pi_callback(self, task_id, **kw):
        """Receive results from Pi workers/controller.

        Body: {state, result, artifacts, token_usage}
        Updates the corresponding ai.coworker.session.
        """
        if not self._check_callback_auth():
            return {'error': 'Unauthorized', 'status': 403}

        body = json.loads(request.httprequest.data or '{}')
        state = body.get('state', 'done')
        result_text = body.get('result', '')
        artifacts = body.get('artifacts', [])
        token_usage = body.get('token_usage', {})

        session = request.env['ai.coworker.session'].sudo().browse(task_id)
        if not session.exists():
            return {'error': 'Session not found', 'status': 404}

        # Update session
        session.write({
            'status': 'done' if state == 'done' else 'error',
            'finish_reason': result_text[:2000] if result_text else state,
        })

        # Save as session line
        next_seq = len(session.session_line_ids) + 1
        request.env['ai.coworker.session.line'].sudo().create({
            'session_id': session.id,
            'sequence': next_seq,
            'role': 'assistant',
            'content': result_text[:4000] if result_text else f'Callback: {state}',
            'token_input': token_usage.get('input', 0),
            'token_output': token_usage.get('output', 0),
            'model_real': 'pi-callback',
        })

        # Save artifacts as attachments
        for art in artifacts:
            if art.get('name') and art.get('data'):
                request.env['ir.attachment'].sudo().create({
                    'name': art['name'],
                    'datas': art['data'],
                    'res_model': 'ai.coworker.session',
                    'res_id': session.id,
                })

        _logger.info('Callback received for session %d: state=%s', task_id, state)
        return {'status': 'ok', 'session_id': session.id}

    @http.route('/pi/bifrost/batch/callback', type='json', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def bifrost_batch_callback(self, **kw):
        """Receive Bifrost batch processing results.

        Body: {batch_id, coworker_id, results, errors}
        Stores results in the corresponding quest session.
        """
        if not self._check_callback_auth():
            return {'error': 'Unauthorized', 'status': 403}

        body = json.loads(request.httprequest.data or '{}')
        batch_id = body.get('batch_id', '')
        coworker_id = body.get('coworker_id')
        results = body.get('results', [])
        errors = body.get('errors', [])

        if not coworker_id:
            return {'error': 'Missing coworker_id', 'status': 400}

        quest = request.env['ai.coworker'].sudo().browse(int(coworker_id))
        if not quest.exists():
            return {'error': 'Quest not found', 'status': 404}

        session = request.env['ai.coworker.session'].sudo().create({
            'coworker_id': quest.id,
            'name': f'Bifrost batch: {batch_id}',
            'status': 'done',
            'user_id': request.env.ref('base.user_root', raise_if_not_found=False).id or 1,
        })

        # Store results
        result_text = json.dumps(results, indent=2)[:4000] if results else ''
        error_text = json.dumps(errors, indent=2)[:2000] if errors else ''
        content = result_text
        if error_text:
            content += f'\n\nErrors:\n{error_text}'

        request.env['ai.coworker.session.line'].sudo().create({
            'session_id': session.id,
            'sequence': 1,
            'role': 'assistant',
            'content': content or f'Batch {batch_id} completed',
            'model_real': 'bifrost-batch',
        })

        _logger.info('Bifrost batch callback: batch=%s quest=%d → session=%d',
                    batch_id, quest.id, session.id)
        return {'status': 'ok', 'session_id': session.id}


class AIOpenAIAPI(http.Controller):
    """OpenAI-compatible API for ai.coworker.

    Enables Pi CLI agents and other OpenAI-compatible clients
    to interact with Odoo quests.
    """

    def _check_api_key(self):
        """Validate API key from Authorization header using Odoo's built-in
        res.users.apikeys. Maps the key to a user and sets request.uid
        so the AI runs with that user's permissions.
        """
        auth = request.httprequest.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return None
        key = auth[7:]

        try:
            user_id = request.env['res.users.apikeys'].sudo()._check_credentials(
                scope='rpc', key=key)
            if user_id:
                request.update_env(user=user_id)
                return request.env.user
        except Exception:
            pass

        return None

    @http.route('/ai/v1/models', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def list_models(self, **kw):
        """GET /ai/v1/models — Lista AI coworkers med API aktiverat."""
        if not self._check_api_key():
            return Response(json.dumps({'error': {'message': 'Unauthorized', 'type': 'authentication_error'}}),
                          status=401, content_type='application/json')

        models = []
        quests = request.env['ai.coworker'].sudo().search(
            [('status', '=', 'active'), ('active', '=', True)],
            order='sequence asc, name asc')

        for q in quests:
            oai = q.init_type_ids.filtered(
                lambda it: it.init_type == 'openai_api' and it.enabled)
            if not oai:
                continue
            alias = self._coworker_alias(q)
            models.append({
                'id': alias,
                'object': 'model',
                'created': int(q.create_date.timestamp()) if q.create_date else 0,
                'owned_by': 'vertel',
                'description': q.sub_description or (q.description[:200] if q.description else ''),
            })

        return Response(json.dumps({'object': 'list', 'data': models}),
                      content_type='application/json')

    @http.route('/ai/v1/<string:coworker>/models', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def coworker_models(self, coworker, **kw):
        """GET /ai/v1/<coworker>/models — Lista modellen för en specifik coworker."""
        if not self._check_api_key():
            return Response(json.dumps({'error': {'message': 'Unauthorized', 'type': 'authentication_error'}}),
                          status=401, content_type='application/json')

        quest = self._resolve_coworker(coworker)
        if not quest:
            return Response(json.dumps({'error': {'message': f"Coworker '{coworker}' not found"}}),
                          status=404, content_type='application/json')

        return Response(json.dumps({'object': 'list', 'data': [{
            'id': self._coworker_alias(quest),
            'object': 'model',
            'owned_by': 'vertel',
        }]}), content_type='application/json')

    @http.route('/ai/v1/sessions/lookup', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def session_lookup(self, **kw):
        """POST /ai/v1/sessions/lookup — find-or-create session via pi_session_id.

        Body: {"pi_session_id": "<uuid>",
               "copy_from_pi_session_id": "<käll-uuid>"}   (copy_from valfritt)
        Auth: Bearer API-nyckel (samma mönster som övriga /ai/v1/*).
        Svar: {session_id, project_id, task_id, partner_id,
               cost_context_confirmed}

        Idempotent: samma pi_session_id → samma session. Vid
        copy_from_pi_session_id (fork) skapas en NY session med kontexten
        (project/task/partner + bekräftelse) kopierad från källsessionen.
        """
        if not self._check_api_key():
            return Response(json.dumps({
                'error': {'message': 'Unauthorized',
                          'type': 'authentication_error'}}),
                status=401, content_type='application/json')
        try:
            body = json.loads(request.httprequest.data or '{}')
        except json.JSONDecodeError:
            return Response(json.dumps({
                'error': {'message': 'Invalid JSON',
                          'type': 'invalid_request_error'}}),
                status=400, content_type='application/json')
        pi_session_id = (body.get('pi_session_id') or '').strip()
        if not pi_session_id:
            return Response(json.dumps({
                'error': {'message': 'Missing pi_session_id',
                          'type': 'invalid_request_error'}}),
                status=400, content_type='application/json')
        copy_from = (body.get('copy_from_pi_session_id') or '').strip()

        Session = request.env['ai.coworker.session'].sudo()
        session, _created = Session._lookup_or_create_pi_session(
            pi_session_id, copy_from_pi_session_id=copy_from)

        def _field_id(name):
            return (session[name].id
                    if name in session._fields and session[name] else None)

        return Response(json.dumps({
            'session_id': session.id,
            'project_id': _field_id('project_id'),
            'task_id': _field_id('task_id'),
            'partner_id': session.partner_id.id if session.partner_id else None,
            'cost_context_confirmed': session.cost_context_confirmed,
        }), content_type='application/json')

    @http.route('/ai/v1/<string:coworker>/chat/completions', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def coworker_chat(self, coworker, **kw):
        """POST /ai/v1/<coworker>/chat/completions — Coworker i URL:en."""
        return self._handle_chat(coworker, **kw)

    @http.route('/ai/v1/chat/completions', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def chat_completions(self, **kw):
        """POST /ai/v1/chat/completions — Coworker i body (model-fältet).
        
        Detta är standard OpenAI-formatet. Pi skickar hit med model=<alias>.
        """
        body = json.loads(request.httprequest.data or '{}')
        coworker = body.get('model', '')
        if not coworker:
            return Response(json.dumps({'error': {'message': 'Missing model', 'type': 'invalid_request_error'}}),
                          status=400, content_type='application/json')
        return self._handle_chat(coworker, **kw)

    def _handle_chat(self, coworker, **kw):
        body = json.loads(request.httprequest.data or '{}')
        messages = body.get('messages', [])
        stream = body.get('stream', True)

        quest = self._resolve_coworker(coworker)
        if not quest:
            return Response(json.dumps({'error': {
                'message': f"Coworker '{coworker}' not found. See /ai/v1/models",
                'type': 'invalid_request_error'
            }}), status=404, content_type='application/json')

        # Kräv att openai_api är aktiverat för denna coworker
        oai = quest.init_type_ids.filtered(
            lambda it: it.init_type == 'openai_api' and it.enabled)
        if not oai:
            return Response(json.dumps({'error': {
                'message': f"Coworker '{coworker}' has no API access. Enable openai_api init type.",
                'type': 'invalid_request_error'
            }}), status=403, content_type='application/json')

        # Auth: validate user API key
        if not self._check_api_key():
            return Response(json.dumps({'error': {'message': 'Unauthorized', 'type': 'authentication_error'}}),
                          status=401, content_type='application/json')

        return self._run_coworker_chat(
            quest, messages, body.get('model', coworker), stream,
            pi_session_id=body.get('pi_session_id', ''),
            session_id=body.get('session_id', 0),
            tools=body.get('tools', []),
            temperature=body.get('temperature', 0.7),
            max_tokens=body.get('max_tokens', 4096),
        )

    # ── Coworker helpers ──────────────────────────────────────────────

    @staticmethod
    def _tool_names(tools):
        """Extrahera verktygsnamn ur OpenAI tool-formatet."""
        out = []
        for t in tools or []:
            fn = t.get('function') if isinstance(t, dict) else None
            if fn and isinstance(fn, dict):
                name = fn.get('name')
                if name:
                    out.append((name, t))
        return out

    @staticmethod
    def _select_relevant_tools(messages, tools):
        """Supervisor-kontextoptimering: välj endast uppgiftsrelevanta tools.

        Alla 50+ verktygs-schemas i payload:en gör att LLM:en sväljer
        tool_calls i content-text och misslyckas (verifierat 2026-08-31:
        2 relevanta verktyg → native tool_calls + 2s; 58 → text-svullnad +
        87s). Detta metod reducerar verktygslistan så att modellen ser en
        liten uppgiftsmatchad uppsättning + ett litet bas-set.

        Verktygsformat (OpenAI): [{'type':'function',
        'function':{'name':...,'description':...,'parameters':...}}].
        """
        # Alltid behåll ett litet bas-set (kärnförmågor oavsett uppgift)
        BAS = {
            'bash', 'read', 'edit', 'write', 'grep', 'find', 'ls',
            'describe_model', 'fetch_url', 'calculator', 'okf_search',
        }

        # Sammanställ uppgiftstexten (senaste meddelanden)
        prompt_text = ' '.join(
            str(m.get('content') or '') for m in (messages or [])
        )[:4000].lower()
        if not prompt_text:
            prompt_text = 'generisk uppgift'

        # Nyckelord → verktygsfamilj (prefix-match på tool-namn)
        RULES = [
            (['zabbix', 'active check', 'monitor', 'host', 'service down'],
             ['zabbix', 'salt', 'service']),
            (['salt', 'minion', 'pillar', 'grain', 'state', 'cmd.run'],
             ['salt']),
            (['wazuh', 'correlat', 'security event', 'cve'],
             ['wazuh', 'driftlarm']),
            (['postgres', 'pg_', 'replication', 'database', 'db '],
             ['pg_', 'postgres']),
            (['caddy', '502', 'gateway', 'reverse prox', 'tls'],
             ['caddy']),
            (['odoo', 'task', 'project', 'cron', 'log'],
             ['odoo_', 'task_', 'prd_', 'logg', 'tail_odoo']),
            (['mail', 'postfix', 'dovecot', 'email'], ['mail', 'postfix', 'dovecot']),
        ]

        named = dict(AIOpenAIAPI._tool_names(tools))
        if not named:
            return tools or []

        # Matcha fram familjer
        selected = set(BAS & set(named.keys()))  # bas-set som faktiskt finns
        for keywords, families in RULES:
            if any(k in prompt_text for k in keywords):
                for fam in families:
                    selected |= {
                        n for n in named if n.startswith(fam)}

        # Fallback: om inget matchade, behåll en kompakt bas-y del (exkl.
        # stora per-domän familjer) så sessionen inte blir helt utan kontext.
        if len(selected) <= len(BAS & set(named.keys())):
            # Ta de 8 första icke-bas verktygen som en kompakt default
            rest = [n for n in named if n not in selected]
            selected |= set(rest[:8])

        # Behåll originalordning
        result = [t for name, t in named.items() if name in selected]
        _logger.info(
            'supervisor tool-select: %d/%d verktyg skickas till LLM (%s)',
            len(result), len(named),
            ','.join(sorted(n for n in named if n in selected))[:300])
        return result

    @staticmethod
    def _coworker_alias(quest):
        """Get a URL-safe alias for a coworker."""
        alias = (quest.channel_alias or '').strip()
        if alias:
            return alias
        return ''.join(
            c if c.isalnum() or c in '-_' else '-'
            for c in quest.name
        ).strip('-').lower() or f'coworker-{quest.id}'

    def _run_coworker_chat(self, quest, messages, model_ref, stream,
                           pi_session_id='', session_id=0, tools=None,
                           temperature=0.7, max_tokens=4096):
        """Execute a chat completion through a coworker as Pi's LLM backend.

        HYBRID (pi+odoo, 2026-08): istället för att köra Odoos egen AgentLoop
        (som exekverar verktyg inuti Odoo och kastar bort Pi:s lokala
        tools), gör denna väg REN generation: Pi:s fullständiga messages-
        historik + tool-schemas (inkl. Pi:s egna bash/ssh/salt) skickas
        oförändrade till coworkerns underliggande LLM. Returnerade
        tool_calls skickas tillbaka till Pi, som exekverar sina LOKALA
        verktyg själv och skickar Odoo-verktyg till /ai/v1/tools/run.
        Sessionen loggas i Odoo som vanligt.
        """
        # ── Imports (must be at method level for both sync + stream paths) ──
        import asyncio
        from odoo.addons.ai_agent_core.core.provider import (
            ProviderFactory, get_default_provider, Message, Role)

        tools = tools or []

        # Extract last user message (för sessionens name + init-prompt)
        user_messages = [m for m in messages if m.get('role') == 'user']
        if not user_messages:
            return Response(json.dumps({'error': {'message': 'No user message provided', 'type': 'invalid_request_error'}}),
                          status=400, content_type='application/json')

        prompt = _content_to_text(user_messages[-1].get('content'))
        system_prompt = quest.description or ''

        # ── Konvertera Pi:s messages → Message-objekt (ren generering) ──
        # Behovar HELA historiken (inkl. role:'tool', tool_calls,
        # tool_call_id) så att Pi:s loop kan fortsätta korrekt.
        def _to_message_list(raw_messages):
            out = []
            for m in raw_messages:
                role = (m.get('role') or '').strip()
                try:
                    role_enum = Role(role)
                except ValueError:
                    continue
                content = _content_to_text(m.get('content'))
                tool_call_id = m.get('tool_call_id')
                name = m.get('name')
                tool_calls = m.get('tool_calls') or None
                if role == 'tool' and not tool_call_id:
                    # OpenAI kräver tool_call_id på tool-roles
                    continue
                out.append(Message(
                    role=role_enum, content=content,
                    tool_call_id=tool_call_id, tool_calls=tool_calls,
                    name=name,
                ))
            return out

        msgs = _to_message_list(messages)
        # Sista assistant-meddelandet får alltid korrekt content om det tomt
        if not msgs:
            return Response(json.dumps({'error': {'message': 'No valid messages', 'type': 'invalid_request_error'}}),
                          status=400, content_type='application/json')

        # ── Supervisor-kontextoptimering (A): Välj RELEVANTA tools per uppgift ──
        # Sänder alla 50+ verktygs-schemas får modellen att SVÄLJA tool_calls i
        # content-text och misslyckas (verifierat: 2 relevanta → native
        # tool_calls + 2s; 58 → text-svullnad + 87s). Här reduceras `tools`-
        # payloaden till en uppgiftsrelevant subset (bas + nyckelordsmatch.
        tools = self._select_relevant_tools(messages, tools)

        # Skills (samma block som ai.coworker.run()): medarbetarens egna +
        # teamets agenters skills. Injiceras i request-kontexten så att även
        # stream-generatorn (körs efter teardown) får med dem via strängen.
        # Gör att /ai/v1-klienter (Pi m.fl.) ser samma kapaciteter som
        # chat-UI:t och run()-vägen.
        try:
            skill_recs = quest.skill_ids | quest.agent_ids.agent_id.skill_ids
            if skill_recs:
                skill_ctx = '\n\n## Skills (följ dessa vid behov)\n' + '\n'.join(
                    f'### {s.name}\n{s.recipe_text or s.description or ""}'
                    for s in skill_recs)
                system_prompt = (system_prompt or '') + skill_ctx
        except Exception as e:
            _logger.warning('skill injection failed (openai_api): %s', e)


        # ── Session (session-cost-context 3.1) ──────────────────────────────
        # Hitta/skapa session via pi_session_id (Pi) eller återanvänd
        # session_id. Sync-vägen gör det i request-transaktionen; stream-vägen
        # i generatorn (egen cursor så den nya sessionen syns).
        def _find_or_create_session(env):
            Sess = env['ai.coworker.session']
            sess = Sess.browse(0)
            if session_id:
                sess = Sess.browse(int(session_id))
                if not sess.exists():
                    sess = Sess.browse(0)
            if not sess and pi_session_id:
                sess = Sess.search(
                    [('pi_session_id', '=', pi_session_id)], limit=1)
            if not sess:
                sess = Sess.create({
                    'coworker_id': quest.id,
                    'status': 'active',
                    'name': (prompt or 'API')[:80],
                    'user_id': env.user.id,
                    'pi_session_id': pi_session_id or False,
                })
            return sess

        def _cost_context_prompt_block(sess):
            """Bygg kostnadskontext-blocket (D9) för systemprompten.

            Injiceras endast för openai_api-körningar. Innehåller aktuell
            session-kontext + coworkerns konfigurerade frågetext +
            "fråga en gång"-instruktion. Tyst no-op vid fel.
            """
            try:
                # OBS (streaming): använder sess.coworker_id (sessionens
                # egen cursor) i stället för closure-variabeln quest —
                # request.cursor är stängd när generatorn körs efter
                # teardown.
                cw = sess.coworker_id if (
                    'coworker_id' in sess._fields and sess.coworker_id
                ) else quest
                has_openai = bool(cw.init_type_ids.filtered(
                    lambda it: it.init_type == 'openai_api' and it.enabled))
                if not has_openai:
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
                question = (cw.cost_context_question or '').strip()
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
                          'cost_context_set (kolla först med '
                          'cost_context_get). Ställ ALDRIG om frågan när '
                          'cost_context_confirmed är satt.\n'
                    )
                else:
                    block += 'Fråga inte om kostnadskontext igen.\n'
                return block
            except Exception as e:
                _logger.warning('cost-context injection failed: %s', e)
                return ''

        def _persist_session(env, sess, response_text, input_t, output_t,
                             model_real='', tool_history=None):
            """Persistera körningen till sessionen (session-cost-context 3.2).

            Bokföring per meddelande: user-raden får requestens input-tokens
            (prompt-kostnaden), assistant-raden får output-tokens + en
            tool_calls-sammanfattning (granskningsbar kontext).
            """
            try:
                Line = env['ai.coworker.session.line']
                sys_mult = 1.0
                if model_real:
                    ai_model = env['ai.model']._resolve_from_real(
                        model_real, sess.coworker_id)
                    if ai_model:
                        sys_mult = ai_model.sys_multiplier
                Line.create({
                    'session_id': sess.id, 'role': 'user',
                    'content': (prompt or '')[:2000], 'sequence': 1,
                    'token_input': input_t, 'token_output': 0,
                    'sys_multiplier': sys_mult,
                })
                Line.create({
                    'session_id': sess.id, 'role': 'assistant',
                    'content': response_text,
                    'token_input': 0, 'token_output': output_t,
                    'model_real': model_real or '', 'sequence': 2,
                    'sys_multiplier': sys_mult,
                    'tool_calls': json.dumps([
                        {'name': n, 'preview': str(p)[:200]}
                        for n, p in (tool_history or [])],
                        ensure_ascii=False),
                })
                for i, (t_name, t_preview) in enumerate(tool_history or []):
                    Line.create({
                        'session_id': sess.id, 'role': 'tool',
                        'tool_name': t_name, 'content': t_preview,
                        'sequence': 10 + i,
                    })
                sess.write({
                    'token_input': (sess.token_input or 0) + input_t,
                    'token_output': (sess.token_output or 0) + output_t,
                })
            except Exception as e:
                _logger.warning('session persist failed: %s', e)

        # Get model from quest's first agent (model_id — inte legacy ai_agent_llm)
        model_name = ''

        for agent_rel in quest.agent_ids:
            if agent_rel.agent_id and agent_rel.agent_id.model_id \
                    and agent_rel.agent_id.model_id.name:
                model_name = agent_rel.agent_id.model_id._get_api_name()
                break
        if not model_name:
            from odoo.addons.ai_agent_core.core.provider import get_default_model_name
            model_name = get_default_model_name()

        coworker_id = quest.id
        # Request-kontext för generatorn (körs efter teardown)
        _gen_dbname = request.env.cr.dbname
        _gen_uid = request.env.uid
        _gen_context = dict(request.env.context)

        if not stream:
            try:
                # Session i request-transaktionen (sync)
                _sess = _find_or_create_session(request.env)
                try:
                    _sess._session_capture_context()
                    _sess._session_auto_capture(prompt)
                except Exception as e:
                    _logger.warning('session capture failed: %s', e)
                system_prompt = (system_prompt or '') + \
                    _cost_context_prompt_block(_sess)
                tool_env = request.env(context=dict(
                    request.env.context,
                    _ai_context_model='ai.coworker.session',
                    _ai_context_id=_sess.id,
                    ai_lineage_session_id=_sess.id,
                ))

                # Coworker = skal, agent = hjärna: auto-skapa default-agenten
                # om medarbetaren saknar agent (samma som _ensure_agent i
                # chat/channel/cron-vägar) så providern aldrig blir None.
                if not quest.agent_ids:
                    try:
                        quest._ensure_agent()
                    except Exception:
                        _logger.exception('Failed to auto-create default agent')
                provider_instance, provider_model = ProviderFactory.from_coworker(quest)
                provider = provider_instance or get_default_provider()[0]
                if provider is None:
                    raise ValueError(
                        'Ingen AI-leverantör konfigurerad för medarbetaren. '
                        'Kontrollera agentens modell eller '
                        'ai_agent_core.default_model_id.')
                # OBS: ProviderFactory returnerar (provider, ai.model-record),
                # INTE modellnamn. Använd model_name (sträng) för provider.chat.
                gen_model = model_name

                # HYBRID: REN generation — skicka Pi:s messages + tools
                # (inkl. Pi:s lokala bash/ssh/salt) oförändrade till LLM:en.
                # Odoo exekverar INTE verktyg här; tool_calls går tillbaka
                # till Pi. Odoo-verktyg körs via /ai/v1/tools/run.
                aloop = asyncio.new_event_loop()
                asyncio.set_event_loop(aloop)
                try:
                    response = aloop.run_until_complete(provider.chat(
                        model=gen_model,
                        messages=msgs,
                        tools=tools,          # Pi:s lokala tool-schemas
                        system_prompt=system_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    ))
                finally:
                    aloop.close()

                response_text = response.text if hasattr(response, 'text') else str(response)
                response_id = f'chatcmpl-{coworker_id}-{fields.Datetime.now().timestamp()}'

                # Bokför anropet. tool_calls (från coworkern) bokförs som
                # granskningsbar kontext på assistant-raden.
                tool_history = [
                    (tc.name, json.dumps(tc.arguments)[:200])
                    for tc in (response.tool_calls or [])]
                _persist_session(
                    tool_env, _sess, response_text,
                    getattr(response, 'input_tokens', 0),
                    getattr(response, 'output_tokens', 0),
                    model_ref, tool_history)
                cost_ctx = {
                    'project_id': (
                        _sess.project_id.id
                        if 'project_id' in _sess._fields and _sess.project_id
                        else None),
                    'task_id': (
                        _sess.task_id.id
                        if 'task_id' in _sess._fields and _sess.task_id
                        else None),
                    'partner_id': _sess.partner_id.id if _sess.partner_id else None,
                    'cost_context_confirmed': _sess.cost_context_confirmed,
                }

                # OpenAI-format och OFFRETFULLT: tool_calls skickas tillbaka
                # till Pi så Pi exekverar dem (lokala tools själv, Odoo-tools
                # via /ai/v1/tools/run).
                msg = {'role': 'assistant', 'content': response_text}
                if response.tool_calls:
                    msg['tool_calls'] = [{
                        'id': tc.id,
                        'type': 'function',
                        'function': {
                            'name': tc.name,
                            'arguments': json.dumps(tc.arguments),
                        },
                    } for tc in response.tool_calls]

                return Response(json.dumps({
                    'id': response_id,
                    'object': 'chat.completion',
                    'created': int(fields.Datetime.now().timestamp()),
                    'model': model_ref,
                    'session_id': _sess.id,
                    'cost_context': cost_ctx,
                    'choices': [{
                        'index': 0,
                        'message': msg,
                        'finish_reason': (
                            response.finish_reason
                            if hasattr(response, 'finish_reason')
                            else 'stop'),
                    }],
                    'usage': {
                        'prompt_tokens': getattr(response, 'input_tokens', 0),
                        'completion_tokens': getattr(response, 'output_tokens', 0),
                        'total_tokens': getattr(response, 'input_tokens', 0) + getattr(response, 'output_tokens', 0),
                    },
                }), content_type='application/json')

            except Exception as e:
                _logger.error('OpenAI API error: %s', e, exc_info=True)
                return Response(json.dumps({
                    'error': {'message': str(e), 'type': 'server_error'}
                }), status=500, content_type='application/json')

        else:
            # Streaming SSE
            _gen_provider, _gen_pmodel = ProviderFactory.from_coworker(quest)
            if not _gen_provider:
                _gen_provider = get_default_provider()[0]
            # OBS: andra elementet är ai.model-record — använd model_name (sträng)
            gen_model = model_name

            def generate():
                full_response = []
                aggregated_tool_calls = []  # (index, {id,name,arguments}) buffrar
                tool_call_delta_buf = {}   # index → {'id','name','arguments','partial_idx'}
                response_id = f'chatcmpl-{coworker_id}-{fields.Datetime.now().timestamp()}'
                created = int(fields.Datetime.now().timestamp())

                try:
                    provider = _gen_provider
                    from odoo import api as _api, registry as _registry
                    _gen_cr = _registry(_gen_dbname).cursor()
                    try:
                        _gen_env = _api.Environment(_gen_cr, _gen_uid, _gen_context)
                        # Session (session-cost-context 3.1): skapas i
                        # generatorns egen cursor så den är synlig (och kan
                        # committas). Injicera kostnadskontext i prompten.
                        _sess = _find_or_create_session(_gen_env)
                        try:
                            _sess._session_capture_context()
                            _sess._session_auto_capture(prompt)
                        except Exception as e:
                            _logger.warning('session capture failed: %s', e)
                        _gen_sys_prompt = (system_prompt or '') + \
                            _cost_context_prompt_block(_sess)
                        _gen_env = _api.Environment(_gen_cr, _gen_uid, dict(
                            _gen_context,
                            _ai_context_model='ai.coworker.session',
                            _ai_context_id=_sess.id,
                            ai_lineage_session_id=_sess.id,
                        ))
                    except Exception:
                        _gen_cr.close()
                        raise

                    # HYBRID: REN streaming-generering — Pi:s messages +
                    # tools skickas oförändrade; tool_calls emitteras till Pi.
                    async def _stream():
                        async for event in provider.chat_stream(
                            model=gen_model,
                            messages=msgs,
                            tools=tools,
                            system_prompt=_gen_sys_prompt,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        ):
                            if event.type == 'thinking':
                                # DeepSeek/OpenRouter reasoning — vidarebefordra
                                yield f'data: {json.dumps({"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model_ref, "choices": [{"index": 0, "delta": {"reasoning_content": event.token}}]})}\n\n'
                            elif event.type == 'token':
                                full_response.append(event.token)
                                yield f'data: {json.dumps({"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model_ref, "choices": [{"index": 0, "delta": {"content": event.token}}]})}\n\n'
                            elif event.type == 'tool_call_start':
                                tc = event.tool_call
                                idx = len(aggregated_tool_calls)
                                aggregated_tool_calls.append({
                                    'id': tc.id, 'name': tc.name,
                                    'arguments': ''})
                                tool_call_delta_buf[idx] = dict(
                                    id=tc.id, name=tc.name, args='')
                                yield f'data: {json.dumps({"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model_ref, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": idx, "id": tc.id, "type": "function", "function": {"name": tc.name, "arguments": ""}}]}}]})}\n\n'
                            elif event.type == 'tool_call_delta':
                                # Delvisa arguments-radsignaler — appenda
                                tc = event.tool_call
                                for idx, buf in tool_call_delta_buf.items():
                                    if buf['id'] == tc.id:
                                        if idx < len(aggregated_tool_calls):
                                            aggregated_tool_calls[idx]['arguments'] += tc.arguments
                                        yield f'data: {json.dumps({"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model_ref, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": idx, "function": {"arguments": tc.arguments}}]}}]})}\n\n'
                                        break
                            elif event.type == 'tool_call_end':
                                tc = event.tool_call
                                # Hitta buffrad tool_call eller lägg till ny.
                                idx = None
                                for i, ent in enumerate(aggregated_tool_calls):
                                    if ent['id'] == tc.id:
                                        idx = i
                                        break
                                full_args = json.dumps(tc.arguments)
                                if idx is None:
                                    aggregated_tool_calls.append({
                                        'id': tc.id, 'name': tc.name,
                                        'arguments': full_args})
                                    idx = len(aggregated_tool_calls) - 1
                                else:
                                    aggregated_tool_calls[idx]['name'] = tc.name
                                    aggregated_tool_calls[idx]['arguments'] = \
                                        full_args
                                # Emittera KOMPLETTA arguments för tool_calls
                                # (providern buffrar och ger inga tool_call_delta
                                # — skicka full args nu så Pi bygger rätt anrop).
                                yield f'data: {json.dumps({"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model_ref, "choices": [{"index": 0, "delta": {"tool_calls": [{"index": idx, "function": {"name": tc.name, "arguments": full_args}}]}}]})}\n\n'
                            elif event.type == 'done':
                                # Verklig usage — bokförs på sessionen.
                                usage_state['input'] = getattr(
                                    event, 'input_tokens', 0) or 0
                                usage_state['output'] = getattr(
                                    event, 'output_tokens', 0) or 0
                                # finish_reason: tool_calls om verktyg, annars ev. stop
                                if aggregated_tool_calls:
                                    yield f'data: {json.dumps({"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model_ref, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})}\n\n'
                                else:
                                    yield f'data: {json.dumps({"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model_ref, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})}\n\n'
                                # Session-info (session-cost-context 3.3)
                                yield f'data: {json.dumps({"session_id": _sess.id, "cost_context": {"project_id": _sess.project_id.id if "project_id" in _sess._fields and _sess.project_id else None, "task_id": _sess.task_id.id if "task_id" in _sess._fields and _sess.task_id else None, "partner_id": _sess.partner_id.id if _sess.partner_id else None, "cost_context_confirmed": _sess.cost_context_confirmed}})}\n\n'
                                yield 'data: [DONE]\n\n'
                            elif event.type == 'error':
                                yield f'data: {json.dumps({"error": {"message": event.message}})}\n\n'
                                yield 'data: [DONE]\n\n'

                    aloop = asyncio.new_event_loop()
                    asyncio.set_event_loop(aloop)
                    usage_state = {'input': 0, 'output': 0}
                    try:
                        async def _collect():
                            result = []
                            async for chunk in _stream():
                                result.append(chunk)
                            return result
                        results = aloop.run_until_complete(_collect())
                    finally:
                        aloop.close()
                    try:
                        tool_history = [
                            (tc['name'], tc['arguments'][:200])
                            for tc in aggregated_tool_calls]
                        _persist_session(
                            _gen_env, _sess, ''.join(full_response),
                            usage_state['input'] or (len(prompt) // 4),
                            usage_state['output'] or (len(''.join(full_response)) // 4),
                            model_ref, tool_history)
                        _gen_cr.commit()
                    finally:
                        _gen_cr.close()

                    for chunk in results:
                        yield chunk

                except Exception as e:
                    try:
                        _gen_cr.close()
                    except Exception:
                        pass
                    _logger.error('OpenAI SSE error: %s', e, exc_info=True)
                    yield f'data: {json.dumps({"error": {"message": str(e)}})}\n\n'
                    yield 'data: [DONE]\n\n'

            return request.make_response(
                generate(),
                headers={
                    'Content-Type': 'text/event-stream',
                    'Cache-Control': 'no-cache',
                    'Connection': 'keep-alive',
                    'X-Accel-Buffering': 'no',
                }
            )

    # ── Odoo-tools för hybriden (pi+odoo) ────────────────────────────

    @http.route('/ai/v1/<string:coworker>/tools', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def coworker_tools(self, coworker, **kw):
        """GET /ai/v1/<coworker>/tools — Odoo-verktygens schemas.

        Hybriden: Pi hämtar coworkerns Odoo-verktyg och registrerar dem som
        Pi-tools med en handler som anropar /ai/v1/tools/run. Coworkern kan
        då använda Odoo-förmågor (describe_model, odoo_search, task_get …)
        samtidigt som Pi behåller sina LOKALA tools (bash/ssh/salt).
        Access-filtreras per autentiserad användares grupper; NATS-verktyg
        utesluts (kräver lyssnande Pi-agent).
        """
        if not self._check_api_key():
            return Response(json.dumps({
                'error': {'message': 'Unauthorized',
                          'type': 'authentication_error'}}),
                status=401, content_type='application/json')

        quest = self._resolve_coworker(coworker)
        if not quest:
            return Response(json.dumps({'error': {
                'message': f"Coworker '{coworker}' not found. See /ai/v1/models",
                'type': 'invalid_request_error'
            }}), status=404, content_type='application/json')

        try:
            from odoo.addons.ai_agent_core.core.tools import (
                ToolRegistry, ai_tool_records_to_tools)
            # Samma verktygsurval som _run_coworker_chat tidigare hämtade:
            # settings-default + explicita verktyg, access-filtrerat.
            tool_ids = quest._session_tool_ids(
                access_groups=request.env.user.groups_id.ids)
            tool_env = request.env(context=dict(
                request.env.context,
                _ai_context_model='ai.coworker.session',
            ))
            reg = ToolRegistry()
            if tool_ids:
                recs = tool_env['ai.tool'].browse(tool_ids).filtered(
                    lambda t: t.executor != 'nats')
                tools = ai_tool_records_to_tools(recs, tool_env)
                reg.register_many(tools)
            schemas = reg.to_openai()
        except Exception as e:
            _logger.error('coworker_tools error: %s', e, exc_info=True)
            return Response(json.dumps({'error': {
                'message': str(e), 'type': 'server_error'}}),
                status=500, content_type='application/json')

        return Response(json.dumps({
            'object': 'list',
            'data': schemas,
            'coworker': self._coworker_alias(quest),
        }), content_type='application/json')

    @http.route('/ai/v1/tools/run', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def tools_run(self, **kw):
        """POST /ai/v1/tools/run — exekvera Odoo-verktyg med full kontext.

        Hybriden återinbäddar: när coworkerns LLM returnerar en tool_call för
        ett Odoo-verktyg (som Pi inte kan exekvera lokalt), skickar Pi den
        hit. Odoo exekverar verktyget med den autentiserade användarens
        rättigheter och sessionens kostnadskontext — aldrig Pi→Odoo-RPC
        direkt. Body: {"session_id": N, "tool_calls": [{id, name, arguments}]}.
        Returnerar {results: [{name, result, is_error}]}.
        """
        if not self._check_api_key():
            return Response(json.dumps({
                'error': {'message': 'Unauthorized',
                          'type': 'authentication_error'}}),
                status=401, content_type='application/json')
        try:
            body = json.loads(request.httprequest.data or '{}')
        except json.JSONDecodeError:
            return Response(json.dumps({
                'error': {'message': 'Invalid JSON',
                          'type': 'invalid_request_error'}}),
                status=400, content_type='application/json')

        session_id = int(body.get('session_id') or 0)
        pi_session_id = (body.get('pi_session_id') or '').strip()
        coworker_ref = (body.get('coworker') or '').strip()
        tool_calls = body.get('tool_calls') or []
        if not tool_calls:
            return Response(json.dumps({
                'error': {'message': 'Missing tool_calls',
                          'type': 'invalid_request_error'}}),
                status=400, content_type='application/json')

        import asyncio
        Sess = request.env['ai.coworker.session'].sudo()
        sess = Sess.browse(session_id) if session_id else Sess.browse(0)
        if not sess.exists() and pi_session_id:
            sess = Sess.search([('pi_session_id', '=', pi_session_id)],
                               limit=1)
        if not sess.exists():
            # Fallback: använd den autentiserade användarens senaste aktiva
            # session för sammanhang; annars körs med user-kontext enbart.
            sess = Sess.search([
                ('user_id', '=', request.env.user.id),
                ('status', '=', 'active'),
            ], limit=1)

        try:
            quest = sess.coworker_id if sess.exists() else Sess.browse(0)
            if not quest.exists() and coworker_ref:
                # Sessionen kan sakna coworker_id (t.ex. Pi-extensionens
                # sessions/lookup-väg) — lös istället via det skickade alias:et.
                quest = self._resolve_coworker(coworker_ref)
            if not quest.exists():
                return Response(json.dumps({'error': {
                    'message': 'No coworker session context; pass a valid session_id or coworker',
                    'type': 'invalid_request_error'}}),
                    status=400, content_type='application/json')

            from odoo.addons.ai_agent_core.core.tools import (
                ToolRegistry, ai_tool_records_to_tools)

            # Autentiserad användares rättigheter (ej sudo för grupp-koll),
            # med session-kontext för verktygens ORM-anrop.
            tool_env = request.env(context=dict(
                request.env.context,
                _ai_context_model='ai.coworker.session',
                _ai_context_id=sess.id,
                ai_lineage_session_id=sess.id,
            ))
            tool_ids = quest._session_tool_ids(
                access_groups=request.env.user.groups_id.ids)
            reg = ToolRegistry()
            if tool_ids:
                recs = tool_env['ai.tool'].browse(tool_ids).filtered(
                    lambda t: t.executor != 'nats')
                reg.register_many(ai_tool_records_to_tools(recs, tool_env))

            aloop = asyncio.new_event_loop()
            asyncio.set_event_loop(aloop)

            async def _run_one(tc):
                name = tc.get('name', '')
                args = tc.get('arguments') or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                if not isinstance(args, dict):
                    args = {}
                # Normalisera nästlad 'arguments'-nyckel (samma som loop.py)
                if 'arguments' in args and set(args.keys()) == {'arguments'}:
                    args = args['arguments']
                tool = reg.get(name)
                if not tool:
                    return {'name': name,
                            'result': f"Unknown or not-allowed tool '{name}'",
                            'is_error': True}
                try:
                    result = await tool.execute(**args)
                    return {'name': name, 'result': str(result),
                            'is_error': False}
                except Exception as e:
                    return {'name': name,
                            'result': f'Tool error ({name}): {e}',
                            'is_error': True}

            try:
                results = aloop.run_until_complete(asyncio.gather(
                    *[_run_one(tc) for tc in tool_calls]))
            finally:
                aloop.close()

            return Response(json.dumps({
                'session_id': sess.id if sess.exists() else 0,
                'results': results,
            }), content_type='application/json')
        except Exception as e:
            _logger.error('tools/run error: %s', e, exc_info=True)
            return Response(json.dumps({'error': {
                'message': str(e), 'type': 'server_error'}}),
                status=500, content_type='application/json')

    # ── Coworker resolution ───────────────────────────────────────────

    def _resolve_coworker(self, model_id):
        """Resolve a coworker from a model identifier.

        Supports:
          - quest-<ID>   (backward compat)
          - channel_alias (e.g. 'redovisning')
          - name slug     (e.g. 'bokslut-britta')
        """
        coworker = request.env['ai.coworker'].sudo()

        # 1. quest-<ID> format
        if model_id.startswith('quest-'):
            try:
                qid = int(model_id.replace('quest-', ''))
                q = coworker.browse(qid)
                if q.exists() and q.status == 'active' and q.active:
                    return q
            except ValueError:
                pass

        # 2. Exact channel_alias match
        q = coworker.search([
            ('channel_alias', '=', model_id),
            ('status', '=', 'active'),
            ('active', '=', True),
        ], limit=1)
        if q:
            return q

        # 3. Name slug match (sanitized name)
        domain = [('status', '=', 'active'), ('active', '=', True)]
        all_coworkers = coworker.search(domain)
        for c in all_coworkers:
            name_slug = ''.join(
                ch if ch.isalnum() or ch in '-_' else '-'
                for ch in c.name
            ).strip('-').lower()
            if name_slug == model_id.lower():
                return c

        return None

    # ── (stub — proxy borttagen, ersatt av _resolve_coworker ovan) ──

    def _chat_completion_model_proxy(self, body, model_name, messages, stream):
        """Deprecated — använd _resolve_coworker + ordinarie AgentLoop."""
        return Response(json.dumps({
            'error': {
                'message': 'Use a coworker name (not a model name). See /ai/v1/models',
                'type': 'invalid_request_error'
            }
        }), status=400, content_type='application/json')

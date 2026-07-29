# -*- coding: utf-8 -*-
"""
SSE Streaming Controller for AI.Quest responses.

Token-by-token streaming via Server-Sent Events.
Uses real BifrostProvider + StreamingAgentLoop (no mock).
"""

import asyncio
import json
import logging
import threading
import time
from html import escape

from odoo import http, fields, api
from odoo.http import request, Response

# Import access control helper (quest-access-control change)
try:
    from odoo.addons.ai_agent_core.models.ai_quest import _quest_is_accessible
except ImportError:
    _quest_is_accessible = None

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SSE Controller
# ---------------------------------------------------------------------------


class AIStreamController(http.Controller):
    """Server-Sent Events endpoint for streaming AI responses."""

    @http.route('/ai/ping', type='http', auth='none', cors='*', sitemap=False)
    def ping(self):
        return request.make_response('pong', [('Content-Type', 'text/plain')])

    @http.route('/ai/stream', type='http', auth='public', cors='*', sitemap=False)
    def stream(self, quest_id=None, prompt=None, session_id=None, **kw):
        """Stream AI response via SSE. Supports session persistence."""
        if not prompt:
            return Response(
                json.dumps({"error": "Missing prompt parameter"}),
                status=400,
                content_type='application/json',
            )

        # Resolve quest configuration
        model = "cerebras/gpt-oss-120b"
        system_prompt = ""
        quest = None

        if quest_id:
            try:
                quest = request.env['ai.quest'].browse(int(quest_id))
                if quest.exists():
                    # Access check
                    if request.env.user and not _quest_is_accessible(quest, request.env.user):
                        return Response(
                            json.dumps({"error": "Quest not accessible"}),
                            status=403,
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
                        cq = request.env['ai.quest'].sudo().browse(int(context_quest))
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
                        if agent.provider_type == 'direct' and agent.direct_model:
                            model = agent.direct_model
                            break
                        elif agent.model_id:
                            model = agent.model_id.name
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

        # Load thread history if session_id provided
        session = None
        history_messages = []
        if session_id:
            try:
                session = request.env['ai.quest.session'].browse(int(session_id))
                if session.exists():
                    # Inject quest memories into system prompt
                    if quest and quest.identity_id:
                        memories_text = _get_quest_memories(quest)
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
                    request.env['ai.quest.session.line'].create({
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
        # DB identity for the post-teardown cursor (tool execution needs an
        # env, but `request.env` is unbound inside the SSE generator)
        gen_dbname = request.env.cr.dbname
        gen_uid = request.env.uid
        gen_context = dict(request.env.context)
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
                            'model': (agent.model_id.name if agent.model_id else model),
                        })
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
                        from odoo.addons.ai_agent_core.core.provider import BifrostProvider
                        from odoo.addons.ai_agent_core.core.tools import ToolRegistry, builtin_tools, wrap_tools_with_env
                        from odoo.addons.ai_agent_core.core.loop import StreamingAgentLoop, AgentConfig
                        from odoo.addons.ai_agent_core.core.supervisor import StreamingSupervisorLoop, SupervisorConfig, SpecialistAgent

                        provider = BifrostProvider(
                            base_url="http://192.168.11.150:8080/v1",
                            virtual_key="opencode",
                        )
                        tools = ToolRegistry()
                        tools.register_many(wrap_tools_with_env(builtin_tools(), gen_env))

                        if gen_is_supervisor and len(gen_agents) > 1:
                            # Build supervisor with streaming
                            specialists = []
                            for a in gen_agents:
                                specialists.append(SpecialistAgent(
                                    name=a['name'],
                                    description=a['description'],
                                    loop=StreamingAgentLoop(
                                        provider=provider, tools=tools,
                                        config=AgentConfig(
                                            model=a['model'],
                                            system_prompt=system_prompt,
                                            max_rounds=10,
                                            nats_api_secret=nats_api_secret,
                                            nats_max_retries=nats_max_retries,
                                        ),
                                    ),
                                ))
                            loop_obj = StreamingSupervisorLoop(
                                router_provider=provider, agents=specialists,
                                config=SupervisorConfig(router_model=model),
                            )
                        else:
                            loop_obj = StreamingAgentLoop(
                                provider=provider,
                                tools=tools,
                                config=AgentConfig(
                                    model=model,
                                    system_prompt=system_prompt,
                                    max_rounds=10,
                                    nats_api_secret=nats_api_secret,
                                    nats_max_retries=nats_max_retries,
                                ),
                            )

                        async for event in loop_obj.run_stream(prompt):
                            data = {"type": event.type}
                            if event.type == "token":
                                data["token"] = event.token
                                full_response.append(event.token)
                            elif event.type == "tool_call_start":
                                if event.tool_call:
                                    data["tool_call"] = {
                                        "id": event.tool_call.id,
                                        "name": event.tool_call.name,
                                    }
                            elif event.type in ("done", "error"):
                                data["finish_reason"] = event.finish_reason
                            yield f"data: {json.dumps(data)}\n\n"

                    # Fresh cursor + env for the post-teardown phase:
                    # tool handlers run ORM calls while streaming.
                    from odoo import api as _api, registry as _registry
                    with _registry(gen_dbname).cursor() as gen_cr:
                        gen_env = _api.Environment(gen_cr, gen_uid, gen_context)
                        results = loop.run_until_complete(_collect(_stream(gen_env)))
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

        # Load quests that have a web_ui init type (filtered by access)
        quests = request.env['ai.quest'].search(
            [('status', '=', 'active')],
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
                lambda it: it.init_type == 'web_ui' and it.active and it.show_in_chat
            )
            if web_ui_init:
                web_ui_quests.append(q)

        quest_items = ''
        for q in web_ui_quests:
            quest_items += (
                f'<option value="{q.id}" data-name="{escape(q.name)}">{escape(q.name)}</option>'
            )

        # Load user's threads (most recent 50)
        thread_items = ''
        if user and user.id:
            sessions = request.env['ai.quest.session'].search([
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
                .replace('<!-- QUEST_OPTIONS -->', quest_items)
                .replace('<!-- THREAD_ITEMS -->', thread_items))
        # no-store: chat_template.html is inline JS — a cached page keeps
        # running stale frontend code after deploys (bit us in production)
        return Response(html, headers=[
            ('Content-Type', 'text/html; charset=utf-8'),
            ('Cache-Control', 'no-store, must-revalidate'),
        ])

    # === Skills API (slash commands) ===

    @http.route('/ai/quest/skills', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def quest_skills(self, quest_id=None, **kw):
        """Return available skills for a quest as JSON.

        Used by the web chat UI to populate the slash-command
        dropdown. Returns skills from the quest's agents,
        identity, and quest-specific copies.
        """
        skills = []
        if quest_id:
            try:
                quest = request.env['ai.quest'].browse(int(quest_id))
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
            skill = request.env['ai.skill'].browse(skill_id)
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
    def quest_models(self, quest_id=None, **kw):
        """Return available models for a quest's agents with is_vision flag.

        Uses ai_agent_core fields only: agent_ids → agent_id.model_id → ai.model.
        """
        models = []
        seen = set()
        if quest_id:
            try:
                quest = request.env['ai.quest'].browse(int(quest_id))
                if quest.exists():
                    for qa in quest.agent_ids:
                        agent = qa.agent_id
                        if not agent.model_id:
                            continue
                        ai_model = agent.model_id
                        model_name = ai_model.name
                        if model_name in seen:
                            continue
                        seen.add(model_name)
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
        """List user's threads."""
        user = request.env.user
        if not user or not user.id:
            return Response(json.dumps({"threads": []}), content_type='application/json')
        sessions = request.env['ai.quest.session'].search([
            ('user_id', '=', user.id),
            ('active', '=', True),
        ], order='write_date desc', limit=50)
        return Response(json.dumps({
            "threads": [{
                "id": s.id,
                "name": s.thread_name or (s.name or 'Tråd'),
                "quest_id": s.quest_id.id if s.quest_id else None,
                "skill_id": s.skill_id.id if s.skill_id else None,
                "last_activity": str(s.write_date) if s.write_date else None,
                "message_count": s.line_count,
            } for s in sessions]
        }), content_type='application/json')

    @http.route('/ai/threads', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def thread_create(self, **kw):
        """Create a new thread."""
        user = request.env.user
        # Parse JSON body for type='http' POST
        body = json.loads(request.httprequest.data or '{}')
        name = body.get('name', 'Ny tråd')
        # Clean name: remove newlines, collapse spaces, trim, limit length
        name = ' '.join(str(name).split())[:50]
        quest_id = body.get('quest_id')
        skill_id = body.get('skill_id')
        # Builder context: the auto-init prompt ("Study this quest…") makes a
        # useless thread name — name the thread after the subject quest instead
        context_quest = body.get('context_quest')
        if context_quest:
            try:
                cq = request.env['ai.quest'].browse(int(context_quest))
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
        if quest_id:
            vals['quest_id'] = int(quest_id)
        if skill_id:
            vals['skill_id'] = int(skill_id)
        session = request.env['ai.quest.session'].create(vals)
        return Response(json.dumps({"id": session.id, "name": session.thread_name}),
                       content_type='application/json')

    @http.route('/ai/threads/<int:thread_id>', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def thread_get(self, thread_id, **kw):
        """Get thread with messages."""
        session = request.env['ai.quest.session'].browse(thread_id)
        if not session.exists():
            return Response(json.dumps({"error": "Thread not found"}), content_type='application/json', status=404)
        lines = session.session_line_ids.sorted('sequence')
        return Response(json.dumps({
            "id": session.id,
            "name": session.thread_name or (session.name or ''),
            "quest_id": session.quest_id.id if session.quest_id else None,
            "messages": [{
                "role": l.role,
                "content": l.content or '',
                "tool_name": l.tool_name,
                "token_sys": l.token_sys or 0,
            } for l in lines]
        }), content_type='application/json')

    @http.route('/ai/threads/<int:thread_id>', type='http', auth='public',
                methods=['PUT'], csrf=False, sitemap=False)
    def thread_rename(self, thread_id, **kw):
        """Rename a thread."""
        body = json.loads(request.httprequest.data or '{}')
        name = body.get('name', '')
        session = request.env['ai.quest.session'].browse(thread_id)
        if session.exists():
            session.thread_name = name[:200]
            session.name = name[:200]
        return Response(json.dumps({"status": "ok"}), content_type='application/json')

    @http.route('/ai/threads/<int:thread_id>', type='http', auth='public',
                methods=['DELETE'], csrf=False, sitemap=False)
    def thread_delete(self, thread_id, **kw):
        """Delete a thread (soft-delete by setting active=False)."""
        session = request.env['ai.quest.session'].browse(thread_id)
        if session.exists():
            session.active = False
        return Response(json.dumps({"status": "ok"}), content_type='application/json')

    @http.route('/ai/threads/<int:thread_id>/respond', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def thread_save_response(self, thread_id, **kw):
        """Save an assistant response to a thread (called by frontend after streaming).
        
        Accepts optional token tracking fields: token_input, token_output, model_real.
        These enable systemtoken computation via sys_multiplier.
        """
        body = json.loads(request.httprequest.data or '{}')
        content = body.get('content', '')
        role = body.get('role', 'assistant')
        model_real = body.get('model_real', '')
        token_input = body.get('token_input', 0)
        token_output = body.get('token_output', 0)
        if not content.strip():
            return Response(json.dumps({"status": "ok"}), content_type='application/json')
        session = request.env['ai.quest.session'].browse(thread_id)
        if session.exists():
            next_seq = len(session.session_line_ids) + 1

            # Resolve sys_multiplier from ai.model if model_real is provided
            sys_mult = 1.0
            if model_real:
                ai_model = request.env['ai.model'].search(
                    [('name', 'ilike', model_real)], limit=1)
                if ai_model:
                    sys_mult = ai_model.sys_multiplier

            request.env['ai.quest.session.line'].create({
                'session_id': session.id,
                'sequence': next_seq,
                'role': role,
                'content': content,
                'token_input': token_input,
                'token_output': token_output,
                'model_real': model_real,
                'sys_multiplier': sys_mult,
            })
            # Also update session totals
            session.token_input += token_input
            session.token_output += token_output
            session.write_date = fields.Datetime.now()

            # Increment quest all-time totals
            if session.quest_id:
                quest = session.quest_id
                quest.total_input_tokens += token_input
                quest.total_output_tokens += token_output
                quest.total_sys_tokens += int((token_input + token_output) * sys_mult)
                # Trigger cap check
                if quest.monthly_cap_mtokens:
                    quest.check_cap()

                # Implicit identity learning (Hole 3)
                if quest.identity_id and quest.identity_id.scope == 'personal':
                    _implicit_learn(quest.identity_id, content)

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

            # Trigger async memory extraction (T8)
            _extract_memories_async(session, content)

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
        lines = request.env['ai.quest.session.line'].search([
            ('content', 'ilike', q),
            ('session_id.user_id', '=', user.id),
            ('session_id.active', '=', True),
        ], limit=50)
        thread_ids = list(set(lines.mapped('session_id.id')))
        sessions = request.env['ai.quest.session'].browse(thread_ids)
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

        quests = request.env['ai.quest'].search([
            ('status', '=', 'active'),
            ('active', '=', True),
            '|',
            ('model_ids.model', '=', model),
            ('model_ids', '=', False),  # No model restriction = available on all
        ], order='sequence asc, name asc')

        # Deduplicate
        quests = quests.sorted('sequence')

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

        POST body: {quest_id, text, model, res_id}
        Returns AI-processed text.
        """
        body = json.loads(request.httprequest.data or '{}')
        quest_id = body.get('quest_id')
        text = body.get('text', '').strip()
        model = body.get('model', '')
        res_id = body.get('res_id')

        if not quest_id or not text:
            return Response(json.dumps({"error": "Missing quest_id or text"}),
                          content_type='application/json', status=400)

        quest = request.env['ai.quest'].browse(int(quest_id))
        if not quest.exists() or quest.init_type != 'powerbox':
            return Response(json.dumps({"error": "Quest not found or not powerbox"}),
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
        quest_id = body.get('quest_id')

        if not learning:
            return Response(json.dumps({"error": "Empty learning"}),
                          content_type='application/json', status=400)

        # Find the quest (personal companion or specified)
        quest = None
        if quest_id:
            quest = request.env['ai.quest'].browse(int(quest_id))
        elif user.personal_quest_id:
            quest = user.personal_quest_id

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

    @http.route('/ai/improve', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def improve_quest(self, **kw):
        """Förbättra-kommando: uppdatera quest med feedback."""
        user = request.env.user
        body = json.loads(request.httprequest.data or '{}')
        quest_id = body.get('quest_id')
        guidance_text = body.get('guidance', '')
        if not guidance_text.strip():
            return Response(json.dumps({"error": "Tom förbättringstext"}),
                          content_type='application/json', status=400)
        quest = request.env['ai.quest'].browse(int(quest_id)) if quest_id else None
        if not quest or not quest.exists():
            return Response(json.dumps({"error": "Quest ej hittad"}),
                          content_type='application/json', status=404)
        is_admin = user.has_group('base.group_system')
        is_owner = quest.user_id and quest.user_id.id == user.id
        if not (is_admin or is_owner):
            return Response(json.dumps({"error": "Saknar rättighet"}),
                          content_type='application/json', status=403)
        memory = request.env['ai.memory'].create({
            'name': f'Forbattring: {guidance_text[:80]}',
            'content': guidance_text,
            'quest_id': quest.id,
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
        """Ladda upp dokument → RAG-minne (text eller FAISS)."""
        quest_id = kw.get('quest_id')
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
        quest = request.env['ai.quest'].browse(int(quest_id)) if quest_id else None

        if memory_type == 'faiss':
            # Create FAISS memory from uploaded document
            try:
                from langchain_core.documents import Document
                doc = Document(page_content=text, metadata={'source': filename})
                memory = request.env['ai.memory'].create({
                    'name': f'FAISS: {filename}',
                    'content': text[:2000],
                    'quest_id': quest.id if quest else None,
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
                    'message': f"'{filename}' indexerat som FAISS ({chunk_count} chunks).",
                }), content_type='application/json')
            except Exception as e:
                _logger.warning('FAISS upload failed, falling back to text: %s', e)
                # Fall through to text mode

        # Plain text upload (existing behavior)
        chunks = _chunk_text(text, 2000)
        memories = []
        for i, chunk in enumerate(chunks):
            m = request.env['ai.memory'].create({
                'name': f'{filename} (del {i+1})' if len(chunks) > 1 else filename,
                'content': chunk,
                'quest_id': quest.id if quest else None,
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


def _get_quest_memories(quest) -> str:
    """Get consolidated + FAISS memories for quest's system prompt."""
    parts = []
    try:
        # Consolidated text memories
        memories = request.env['ai.memory'].search([
            ('quest_id', '=', quest.id),
            ('consolidated', '=', True),
            ('archived', '=', False),
        ], limit=20)
        if memories:
            items = [f"- {m.content}" for m in memories]
            parts.append("## Lärt om denna quest\n" + "\n".join(items))
    except Exception:
        pass

    # Only do FAISS search if we have a prompt to search against
    return "\n\n".join(parts) if parts else ""


async def _collect(agen):
    """Collect all items from an async generator into a list."""
    result = []
    async for item in agen:
        result.append(item)
    return result


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


def _summarize_history(session, lines):
    """T7.6: Summarize thread history when > 50 messages.
    
    Uses a simple approach: take first message as context,
    last 5 messages as recent context, drop the middle.
    This avoids an expensive LLM call for summarization.
    """
    if len(lines) <= 50:
        return None
    
    first_msg = lines[0].content[:200] if lines and lines[0].content else 'Start'
    recent = '\n'.join(
        f"[{l.role}] {l.content[:100]}"
        for l in lines[-5:]
        if l.content
    )
    return (
        f"[Tidigare konversation ({len(lines)} meddelanden) sammanfattad. "
        f"Första meddelandet: {first_msg}. "
        f"Senaste: {recent}]"
    )


def _extract_memories_async(session, assistant_content):
    """T8.1-T8.4: Extract key facts from conversation as ai.memory.
    
    Runs asynchronously after each assistant response.
    Uses heuristic extraction (fast, no LLM cost) + stores as ai.memory.
    """
    if not session or not assistant_content:
        return

    try:
        quest = session.quest_id
        if not quest:
            return

        memories = []
        content_lower = assistant_content.lower()

        # Heuristic extraction — fast, no API cost
        if any(kw in content_lower for kw in ('svenska', 'swedish', 'bokföring', 'moms', 'redovisning')):
            memories.append({
                'fact': 'Användaren arbetar med svensk ekonomi/redovisning',
                'category': 'preference',
                'importance': 'medium',
            })

        if any(kw in content_lower for kw in ('csv', 'excel', 'export', 'ladda ner', 'fil')):
            memories.append({
                'fact': 'Användaren efterfrågar dataexport',
                'category': 'fact',
                'importance': 'low',
            })

        if len(assistant_content) < 200:
            memories.append({
                'fact': 'Kort svar gavs — användaren kan föredra koncisa svar',
                'category': 'preference',
                'importance': 'low',
            })

        # Store as ai.memory
        for m in memories:
            request.env['ai.memory'].create({
                'name': m['fact'][:80],
                'content': m['fact'],
                'quest_id': quest.id,
                'category': m['category'],
                'importance': m['importance'],
                'source_thread_id': session.id,
            })

        if memories:
            _logger.debug('Extracted %d memories from session %s',
                         len(memories), session.id)

    except Exception as e:
        _logger.debug('Memory extraction skipped: %s', e)


def _implicit_learn(identity, assistant_content):
    """Extract learnings from assistant response for identity (Hole 3).
    
    Looks for patterns in the assistant's response that indicate
    user preferences or context. Very lightweight — no extra LLM call.
    Uses simple heuristics rather than another API call to keep costs low.
    """
    if not identity or not assistant_content:
        return

    learnings = []
    content_lower = assistant_content.lower()

    # Heuristic: if assistant explains something in Swedish, user prefers Swedish
    if any(word in content_lower for word in ('svenska', 'bokföring', 'moms', 'faktura',
                                                'redovisning', 'deklaration')):
        if 'swedish' not in (identity.user_model or '').lower():
            learnings.append('Användaren arbetar med svensk ekonomi/redovisning')

    # Heuristic: if assistant provides CSV/Excel exports, user wants structured data
    if any(word in content_lower for word in ('csv', 'excel', 'export', 'fil', 'ladda ner')):
        if 'strukturerad' not in (identity.user_model or '').lower():
            learnings.append('Användaren efterfrågar ofta dataexport (CSV/Excel)')

    # Heuristic: short response → user may prefer brevity
    if len(assistant_content) < 300:
        if 'kortfattad' not in (identity.style or '').lower():
            # Only add if this pattern repeats (tracked via memory, not here)
            pass  # Too aggressive for a single sample — let /learn handle this explicitly

    if learnings:
        new_model = (identity.user_model or '') + '\n' + '\n'.join(f'- {l}' for l in learnings)
        identity.user_model = new_model[:4000]
        _logger.info('Implicit learn: added %d facts to identity %s',
                     len(learnings), identity.name)


def _detect_and_suggest_mission(session_id, last_response, company_id, threshold=0.7):
    """Async thread: detect mission gap and suggest update via ai.company.memory."""
    try:
        with api.Environment.manage():
            env = api.Environment(request.env.cr, request.env.uid, request.env.context)
            session = env['ai.quest.session'].browse(session_id)
            if not session.exists():
                return

            quest = session.quest_id
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
        Updates the corresponding ai.quest.session.
        """
        if not self._check_callback_auth():
            return {'error': 'Unauthorized', 'status': 403}

        body = json.loads(request.httprequest.data or '{}')
        state = body.get('state', 'done')
        result_text = body.get('result', '')
        artifacts = body.get('artifacts', [])
        token_usage = body.get('token_usage', {})

        session = request.env['ai.quest.session'].browse(task_id)
        if not session.exists():
            return {'error': 'Session not found', 'status': 404}

        # Update session
        session.write({
            'status': 'done' if state == 'done' else 'error',
            'finish_reason': result_text[:2000] if result_text else state,
        })

        # Save as session line
        next_seq = len(session.session_line_ids) + 1
        request.env['ai.quest.session.line'].create({
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
                request.env['ir.attachment'].create({
                    'name': art['name'],
                    'datas': art['data'],
                    'res_model': 'ai.quest.session',
                    'res_id': session.id,
                })

        _logger.info('Callback received for session %d: state=%s', task_id, state)
        return {'status': 'ok', 'session_id': session.id}

    @http.route('/pi/zabbix/webhook', type='json', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def zabbix_webhook(self, **kw):
        """Receive Zabbix alert webhooks.

        Body: {host, trigger, severity, ...}
        Creates an ai.quest.session for the designated infrastructure quest.
        """
        if not self._check_callback_auth():
            return {'error': 'Unauthorized', 'status': 403}

        body = json.loads(request.httprequest.data or '{}')
        host = body.get('host', 'unknown')
        trigger = body.get('trigger', '')
        severity = body.get('severity', 'warning')

        # Find infrastructure quest (first active quest with 'cron' or 'manual' type)
        quest = request.env['ai.quest'].search(
            [('status', '=', 'active')], limit=1, order='sequence asc')
        if not quest:
            return {'error': 'No active quest found', 'status': 404}

        # Create session with alert context
        session = request.env['ai.quest.session'].create({
            'quest_id': quest.id,
            'name': f'Zabbix: {trigger[:100]}',
            'status': 'active',
            'user_id': request.env.ref('base.user_root', raise_if_not_found=False).id or 1,
        })

        # Save alert as session line
        prompt = f'⚠️ Zabbix Alert [{severity.upper()}]\nHost: {host}\nTrigger: {trigger}\n\nPlease analyze this alert and recommend actions.'
        request.env['ai.quest.session.line'].create({
            'session_id': session.id,
            'sequence': 1,
            'role': 'user',
            'content': prompt,
        })

        _logger.info('Zabbix webhook: host=%s trigger=%s → session=%d',
                    host, trigger, session.id)
        return {'status': 'ok', 'session_id': session.id}

    @http.route('/pi/bifrost/batch/callback', type='json', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def bifrost_batch_callback(self, **kw):
        """Receive Bifrost batch processing results.

        Body: {batch_id, quest_id, results, errors}
        Stores results in the corresponding quest session.
        """
        if not self._check_callback_auth():
            return {'error': 'Unauthorized', 'status': 403}

        body = json.loads(request.httprequest.data or '{}')
        batch_id = body.get('batch_id', '')
        quest_id = body.get('quest_id')
        results = body.get('results', [])
        errors = body.get('errors', [])

        if not quest_id:
            return {'error': 'Missing quest_id', 'status': 400}

        quest = request.env['ai.quest'].browse(int(quest_id))
        if not quest.exists():
            return {'error': 'Quest not found', 'status': 404}

        session = request.env['ai.quest.session'].create({
            'quest_id': quest.id,
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

        request.env['ai.quest.session.line'].create({
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
    """OpenAI-compatible API for ai.quest.

    Enables Pi CLI agents and other OpenAI-compatible clients
    to interact with Odoo quests.
    """

    def _check_api_key(self):
        """Validate API key from Authorization header.

        Phase 1: Shared secret from system parameter / env (see
                 ``_get_callback_secret``).
        Phase 2: Per-quest keys from ai.quest.init_type.openai_api.
        """
        auth = request.httprequest.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return None
        key = auth[7:]
        if key != _get_callback_secret():
            return None
        return True

    @http.route('/ai/v1/models', type='http', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def list_models(self, **kw):
        """List quests available via OpenAI API."""
        if not self._check_api_key():
            return Response(json.dumps({'error': {'message': 'Unauthorized', 'type': 'authentication_error'}}),
                          status=401, content_type='application/json')

        quests = request.env['ai.quest'].search([('status', '=', 'active')])
        models = []
        for q in quests:
            models.append({
                'id': f'quest-{q.id}',
                'object': 'model',
                'created': int(q.create_date.timestamp()) if q.create_date else 0,
                'owned_by': 'vertel',
            })

        return Response(json.dumps({'object': 'list', 'data': models}),
                      content_type='application/json')

    @http.route('/ai/v1/chat/completions', type='http', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def chat_completions(self, **kw):
        """OpenAI-compatible chat completions with SSE streaming."""
        if not self._check_api_key():
            return Response(json.dumps({'error': {'message': 'Unauthorized', 'type': 'authentication_error'}}),
                          status=401, content_type='application/json')

        body = json.loads(request.httprequest.data or '{}')
        model = body.get('model', '')
        messages = body.get('messages', [])
        stream = body.get('stream', True)

        # Parse quest ID from model (format: quest-42)
        if not model.startswith('quest-'):
            return Response(json.dumps({'error': {'message': f'Model {model} not found', 'type': 'invalid_request_error'}}),
                          status=404, content_type='application/json')

        try:
            quest_id = int(model.replace('quest-', ''))
        except ValueError:
            return Response(json.dumps({'error': {'message': f'Invalid model: {model}', 'type': 'invalid_request_error'}}),
                          status=400, content_type='application/json')

        quest = request.env['ai.quest'].browse(quest_id)
        if not quest.exists():
            return Response(json.dumps({'error': {'message': f'Quest {quest_id} not found', 'type': 'invalid_request_error'}}),
                          status=404, content_type='application/json')

        # Extract last user message
        user_messages = [m for m in messages if m.get('role') == 'user']
        if not user_messages:
            return Response(json.dumps({'error': {'message': 'No user message provided', 'type': 'invalid_request_error'}}),
                          status=400, content_type='application/json')

        prompt = user_messages[-1].get('content', '')
        system_prompt = quest.description or ''

        # Inject conversation history as context
        history_context = ''
        if len(messages) > 1:
            history_lines = []
            for m in messages[:-1]:
                role = m.get('role', 'user')
                content = m.get('content', '')
                history_lines.append(f'[{role}] {content[:500]}')
            history_context = '\n'.join(history_lines[-20:])

        if history_context:
            system_prompt += f'\n\n## Previous conversation\n{history_context}'

        # Get model from quest's first agent
        model_name = 'cerebras/gpt-oss-120b'
        for agent_rel in quest.agent_ids:
            if agent_rel.agent_id and hasattr(agent_rel.agent_id, 'ai_agent_llm_id'):
                llm = agent_rel.agent_id.ai_agent_llm_id
                if llm and llm.model_name:
                    model_name = llm.model_name
                    break

        # NATS executor config (tool-executor-nats)
        _nats_api_secret = request.env['ir.config_parameter'].sudo().get_param(
            'ai_agent_core.api_secret', '')
        _nats_max_retries = int(request.env['ir.config_parameter'].sudo().get_param(
            'pi.nats.max_retries', '3'))
        # DB identity for the post-teardown cursor in the SSE generator
        _gen_dbname = request.env.cr.dbname
        _gen_uid = request.env.uid
        _gen_context = dict(request.env.context)

        if not stream:
            # Non-streaming: run and return full response
            try:
                import asyncio
                from odoo.addons.ai_agent_core.core.provider import BifrostProvider
                from odoo.addons.ai_agent_core.core.tools import ToolRegistry, builtin_tools, wrap_tools_with_env
                from odoo.addons.ai_agent_core.core.loop import AgentLoop, AgentConfig

                provider = BifrostProvider(
                    base_url='http://192.168.11.150:8080/v1',
                    virtual_key='opencode',
                )
                tools = ToolRegistry()
                tools.register_many(wrap_tools_with_env(builtin_tools(), request.env))

                loop_obj = AgentLoop(
                    provider=provider, tools=tools,
                    config=AgentConfig(
                        model=model_name, system_prompt=system_prompt,
                        max_rounds=10,
                        nats_api_secret=_nats_api_secret,
                        nats_max_retries=_nats_max_retries,
                    ),
                )

                aloop = asyncio.new_event_loop()
                asyncio.set_event_loop(aloop)
                try:
                    response = aloop.run_until_complete(loop_obj.run(prompt))
                finally:
                    aloop.close()

                response_text = response.text if hasattr(response, 'text') else str(response)
                response_id = f'chatcmpl-{quest_id}-{fields.Datetime.now().timestamp()}'

                return Response(json.dumps({
                    'id': response_id,
                    'object': 'chat.completion',
                    'created': int(fields.Datetime.now().timestamp()),
                    'model': model,
                    'choices': [{
                        'index': 0,
                        'message': {'role': 'assistant', 'content': response_text},
                        'finish_reason': 'stop',
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
            # Streaming SSE response
            def generate():
                full_response = []
                response_id = f'chatcmpl-{quest_id}-{fields.Datetime.now().timestamp()}'
                created = int(fields.Datetime.now().timestamp())

                try:
                    import asyncio
                    from odoo.addons.ai_agent_core.core.provider import BifrostProvider
                    from odoo.addons.ai_agent_core.core.tools import ToolRegistry, builtin_tools, wrap_tools_with_env
                    from odoo.addons.ai_agent_core.core.loop import StreamingAgentLoop, AgentConfig

                    provider = BifrostProvider(
                        base_url='http://192.168.11.150:8080/v1',
                        virtual_key='opencode',
                    )
                    from odoo import api as _api, registry as _registry
                    _gen_cr = _registry(_gen_dbname).cursor()
                    try:
                        tools = ToolRegistry()
                        tools.register_many(wrap_tools_with_env(
                            builtin_tools(),
                            _api.Environment(_gen_cr, _gen_uid, _gen_context)))
                    except Exception:
                        _gen_cr.close()
                        raise

                    loop = StreamingAgentLoop(
                        provider=provider, tools=tools,
                        config=AgentConfig(
                            model=model_name, system_prompt=system_prompt,
                            max_rounds=10,
                            nats_api_secret=_nats_api_secret,
                            nats_max_retries=_nats_max_retries,
                        ),
                    )

                    async def _stream():
                        async for event in loop.run_stream(prompt):
                            if event.type == 'token':
                                full_response.append(event.token)
                                yield f'data: {json.dumps({"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {"content": event.token}}]})}\n\n'
                            elif event.type == 'done':
                                yield f'data: {json.dumps({"id": response_id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})}\n\n'
                                yield 'data: [DONE]\n\n'
                            elif event.type == 'error':
                                yield f'data: {json.dumps({"error": {"message": event.message}})}\n\n'
                                yield 'data: [DONE]\n\n'

                    aloop = asyncio.new_event_loop()
                    asyncio.set_event_loop(aloop)
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

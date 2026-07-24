# -*- coding: utf-8 -*-
"""
SSE Streaming Controller for AI.Quest responses.

Token-by-token streaming via Server-Sent Events.
Uses real BifrostProvider + StreamingAgentLoop (no mock).
"""

import asyncio
import json
import logging
import time
from html import escape

from odoo import http, fields
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
                    llm_ids = quest.ai_agent_ids.filtered(
                        lambda a: a.ai_agent_id.ai_agent_llm_id
                    )
                    if llm_ids:
                        llm = llm_ids[0].ai_agent_id.ai_agent_llm_id
                        if llm.model_name:
                            model = llm.model_name
            except Exception:
                pass

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
                        summary = '[...Tidigare konversation sammanfattad...]'
                        history_messages = [{'role': 'system', 'content': summary}] + history_messages[-20:]
            except Exception:
                pass

        # Save user message as session line
        user_id = request.env.user.id if request.env.user else None
        if session and user_id:
            next_seq = len(session.session_line_ids) + 1
            request.env['ai.quest.session.line'].create({
                'session_id': session.id,
                'sequence': next_seq,
                'role': 'user',
                'content': prompt,
            })
            session.write_date = fields.Datetime.now()

        def generate():
            """SSE event generator — runs async loop in sync context."""
            try:
                _logger.info("SSE stream starting — prompt: %s...", prompt[:50])
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    async def _stream():
                        from odoo.addons.ai_agent_core.core.provider import BifrostProvider
                        from odoo.addons.ai_agent_core.core.tools import ToolRegistry, builtin_tools
                        from odoo.addons.ai_agent_core.core.loop import StreamingAgentLoop, AgentConfig

                        provider = BifrostProvider(
                            base_url="http://192.168.11.150:8080/v1",
                            virtual_key="opencode",
                        )
                        tools = ToolRegistry()
                        tools.register_many(builtin_tools())

                        loop_obj = StreamingAgentLoop(
                            provider=provider,
                            tools=tools,
                            config=AgentConfig(
                                model=model,
                                system_prompt=system_prompt,
                                max_rounds=10,
                            ),
                        )

                        async for event in loop_obj.run_stream(prompt):
                            data = {"type": event.type}
                            if event.type == "token":
                                data["token"] = event.token
                            elif event.type == "tool_call_start":
                                if event.tool_call:
                                    data["tool_call"] = {
                                        "id": event.tool_call.id,
                                        "name": event.tool_call.name,
                                    }
                            elif event.type in ("done", "error"):
                                data["finish_reason"] = event.finish_reason
                            yield f"data: {json.dumps(data)}\n\n"

                    results = loop.run_until_complete(_collect(_stream()))
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

        # Load quests (filtered by access)
        quests = request.env['ai.quest'].search(
            [('status', '=', 'active')],
            order='sequence asc, name asc',
        )
        accessible_quests = []
        if _quest_is_accessible and user:
            for q in quests:
                if _quest_is_accessible(q, user):
                    accessible_quests.append(q)
        else:
            for q in quests:
                if q.show_in_chat and not q.group_ids and not q.user_ids:
                    accessible_quests.append(q)

        quest_items = ''
        for q in accessible_quests:
            quest_items += (
                f'<div class="quest-item" data-id="{q.id}" data-name="{q.name}">'
                f'<span class="quest-icon">🎯</span>{q.name}</div>'
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
                .replace('<!-- QUEST_ITEMS -->', quest_items)
                .replace('<!-- THREAD_ITEMS -->', thread_items))
        return Response(html, headers=[('Content-Type', 'text/html; charset=utf-8')])

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

    @http.route('/ai/threads', type='json', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def thread_list(self, **kw):
        """List user's threads."""
        user = request.env.user
        if not user or not user.id:
            return {"threads": []}
        sessions = request.env['ai.quest.session'].search([
            ('user_id', '=', user.id),
            ('active', '=', True),
        ], order='write_date desc', limit=50)
        return {
            "threads": [{
                "id": s.id,
                "name": s.thread_name or (s.name or 'Tråd'),
                "quest_id": s.quest_id.id if s.quest_id else None,
                "last_activity": s.write_date,
                "message_count": len(s.session_line_ids),
            } for s in sessions]
        }

    @http.route('/ai/threads', type='json', auth='public',
                methods=['POST'], csrf=False, sitemap=False)
    def thread_create(self, **kw):
        """Create a new thread."""
        user = request.env.user
        data = request.jsonrequest if hasattr(request, 'jsonrequest') else kw
        name = data.get('name', 'Ny tråd')
        quest_id = data.get('quest_id')
        vals = {
            'user_id': user.id if user.id else None,
            'thread_name': name[:200],
            'status': 'active',
        }
        if quest_id:
            vals['quest_id'] = int(quest_id)
        session = request.env['ai.quest.session'].create(vals)
        return {"id": session.id, "name": session.thread_name}

    @http.route('/ai/threads/<int:thread_id>', type='json', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def thread_get(self, thread_id, **kw):
        """Get thread with messages."""
        session = request.env['ai.quest.session'].browse(thread_id)
        if not session.exists():
            return {"error": "Thread not found"}
        lines = session.session_line_ids.sorted('sequence')
        return {
            "id": session.id,
            "name": session.thread_name or (session.name or ''),
            "messages": [{
                "role": l.role,
                "content": l.content or '',
                "tool_name": l.tool_name,
            } for l in lines]
        }

    @http.route('/ai/threads/<int:thread_id>', type='json', auth='public',
                methods=['PUT'], csrf=False, sitemap=False)
    def thread_rename(self, thread_id, **kw):
        """Rename a thread."""
        data = request.jsonrequest if hasattr(request, 'jsonrequest') else kw
        name = data.get('name', '')
        session = request.env['ai.quest.session'].browse(thread_id)
        if session.exists():
            session.thread_name = name[:200]
        return {"status": "ok"}

    @http.route('/ai/threads/<int:thread_id>', type='json', auth='public',
                methods=['DELETE'], csrf=False, sitemap=False)
    def thread_delete(self, thread_id, **kw):
        """Soft-delete a thread."""
        session = request.env['ai.quest.session'].browse(thread_id)
        if session.exists():
            session.active = False
        return {"status": "ok"}

    @http.route('/ai/thread/search', type='json', auth='public',
                methods=['GET'], csrf=False, sitemap=False)
    def thread_search(self, q='', **kw):
        """Search threads by message content."""
        user = request.env.user
        if not q or len(q) < 2 or not user or not user.id:
            return {"threads": []}
        lines = request.env['ai.quest.session.line'].search([
            ('content', 'ilike', q),
            ('session_id.user_id', '=', user.id),
            ('session_id.active', '=', True),
        ], limit=50)
        thread_ids = list(set(lines.mapped('session_id.id')))
        sessions = request.env['ai.quest.session'].browse(thread_ids)
        return {
            "threads": [{
                "id": s.id,
                "name": s.thread_name or (s.name or 'Tråd'),
                "last_activity": s.write_date,
            } for s in sessions]
        }


# ---------------------------------------------------------------------------
# Helper: WebUI handler registry (in-memory, per Odoo worker)
# ---------------------------------------------------------------------------

_webui_handlers: dict[str, 'WebUIInterruptHandler'] = {}

def _get_webui_handler(session_uuid: str):
    return _webui_handlers.get(session_uuid)

def _register_webui_handler(session_uuid: str, handler):
    _webui_handlers[session_uuid] = handler

def _unregister_webui_handler(session_uuid: str):
    _webui_handlers.pop(session_uuid, None)


def _get_quest_memories(quest) -> str:
    """Get consolidated memories for quest's system prompt."""
    try:
        memories = request.env['ai.memory'].search([
            ('quest_id', '=', quest.id),
            ('consolidated', '=', True),
            ('archived', '=', False),
        ], limit=20)
        if memories:
            items = [f"- {m.content}" for m in memories]
            return "## Lärt om denna quest\n" + "\n".join(items)
    except Exception:
        pass
    return ""


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

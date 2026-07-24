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

        def generate():
            """SSE event generator — runs async loop in sync context."""
            full_response = []
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
                "last_activity": str(s.write_date) if s.write_date else None,
                "message_count": len(s.session_line_ids),
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
        vals = {
            'name': name,
            'user_id': user.id if user.id else None,
            'thread_name': name,
            'status': 'active',
        }
        if quest_id:
            vals['quest_id'] = int(quest_id)
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

            _logger.info("Saved response to session %s: %d in/%d out tokens, model=%s",
                        thread_id, token_input, token_output, model_real or 'unknown')
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
        """Ladda upp dokument -> RAG-minne."""
        quest_id = kw.get('quest_id')
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

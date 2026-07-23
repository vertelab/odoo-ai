# -*- coding: utf-8 -*-
"""
SSE Streaming Controller for AI.Quest responses.

Token-by-token streaming via Server-Sent Events.
Uses real BifrostProvider + StreamingAgentLoop (no mock).
"""

import asyncio
import json
import logging

from odoo import http
from odoo.http import request, Response

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
    def stream(self, quest_id=None, prompt=None, **kw):
        """Stream AI response via SSE using real provider."""
        if not prompt:
            return Response(
                json.dumps({"error": "Missing prompt parameter"}),
                status=400,
                content_type='application/json',
            )

        # Resolve quest configuration
        model = "gpt-4o"
        system_prompt = ""
        quest_name = "default"

        if quest_id:
            try:
                quest = request.env['ai.quest'].browse(int(quest_id))
                if quest.exists():
                    quest_name = quest.name
                    if quest.description:
                        system_prompt = quest.description
                    # Use quest's LLM if configured
                    llm_ids = quest.ai_agent_ids.filtered(
                        lambda a: a.ai_agent_id.ai_agent_llm_id
                    )
                    if llm_ids:
                        llm = llm_ids[0].ai_agent_id.ai_agent_llm_id
                        if llm.model_name:
                            model = llm.model_name
            except Exception:
                pass

        def generate():
            """SSE event generator — runs async loop in sync context."""
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    async def _stream():
                        from ..core.provider import BifrostProvider
                        from ..core.tools import ToolRegistry, builtin_tools
                        from ..core.loop import StreamingAgentLoop, AgentConfig

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
                                data["tool_call"] = {
                                    "id": event.tool_call.id if event.tool_call else "",
                                    "name": event.tool_call.name if event.tool_call else "",
                                }
                            elif event.type in ("done", "error"):
                                data["finish_reason"] = event.finish_reason
                            yield f"data: {json.dumps(data)}\n\n"

                    async_gen = _stream()

                    async def consume():
                        async for chunk in async_gen:
                            yield chunk

                    for chunk in loop.run_until_complete(_collect(consume())):
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
        """Render standalone AI chat interface."""
        quests = request.env['ai.quest'].search(
            [('status', '=', 'active')],
            order='sequence asc, name asc',
        )
        quest_items = ''
        for q in quests:
            quest_items += (
                f'<div class="quest-item" data-id="{q.id}" data-name="{q.name}">'
                f'<span class="quest-icon">🎯</span>{q.name}</div>'
            )

        html = _CHAT_HTML.replace('<!-- QUEST_ITEMS -->', quest_items)
        return Response(html, headers=[('Content-Type', 'text/html; charset=utf-8')])


# ---------------------------------------------------------------------------
# Helper: collect async generator into list (for sync→async bridge)
# ---------------------------------------------------------------------------

async def _collect(agen):
    """Collect all items from an async generator into a list."""
    result = []
    async for item in agen:
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# Chat UI HTML template
# ---------------------------------------------------------------------------

_CHAT_HTML = '''<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>AI Chat</title>
<style>
:root {
    --bg: #1a1a2e; --bg-sidebar: #16213e; --bg-message-user: #0f3460;
    --bg-message-ai: #16213e; --bg-input: #16213e; --text: #e0e0e0;
    --text-muted: #888; --accent: #e94560; --accent-hover: #ff6b81;
    --border: #2a2a4a; --agent-card: #1a2744; --radius: 10px;
    --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:var(--font);background:var(--bg);color:var(--text);overflow:hidden}
.chat-app{display:flex;height:100vh}
.sidebar{width:280px;background:var(--bg-sidebar);display:flex;flex-direction:column;border-right:1px solid var(--border);flex-shrink:0}
.sidebar-header{padding:16px;font-size:18px;font-weight:700;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px}
.quest-list{flex:1;overflow-y:auto;padding:8px}
.quest-item{padding:10px 12px;border-radius:var(--radius);cursor:pointer;margin-bottom:2px;font-size:14px;transition:background .15s;display:flex;align-items:center;gap:8px}
.quest-item:hover{background:rgba(255,255,255,0.05)}
.quest-item.active{background:rgba(233,69,96,0.15);color:var(--accent)}
.quest-icon{font-size:18px}
.chat-main{flex:1;display:flex;flex-direction:column;min-width:0}
.chat-header{padding:12px 20px;border-bottom:1px solid var(--border);font-size:14px;color:var(--text-muted);display:flex;align-items:center;gap:8px}
.chat-header strong{color:var(--text)}
.messages{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:16px}
.message{max-width:85%;padding:12px 16px;border-radius:var(--radius);font-size:14px;line-height:1.6;animation:fadeIn .2s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
.message.user{align-self:flex-end;background:var(--bg-message-user);border-bottom-right-radius:4px}
.message.ai{align-self:flex-start;background:var(--bg-message-ai);border:1px solid var(--border);border-bottom-left-radius:4px;max-width:100%;white-space:pre-wrap;width:100%}
.message.ai strong{color:var(--accent)}
.agent-card{background:var(--agent-card);border:1px solid var(--border);border-radius:8px;margin:8px 0;padding:12px}
.agent-card-header{display:flex;align-items:center;gap:8px;font-weight:600;margin-bottom:6px;cursor:pointer;font-size:13px;user-select:none}
.agent-card-body{font-size:13px}
.tool-call{font-size:12px;color:var(--text-muted);padding:4px 8px;background:rgba(255,255,255,0.03);border-radius:4px;margin:4px 0;font-family:monospace}
.tool-result{font-size:12px;color:#4caf50;padding:4px 8px;margin:2px 0}
.chat-input-area{padding:16px 20px;border-top:1px solid var(--border)}
.chat-input-row{display:flex;gap:8px}
.chat-input-row input{flex:1;padding:12px 16px;background:var(--bg-input);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-size:14px;font-family:var(--font);outline:none}
.chat-input-row input:focus{border-color:var(--accent)}
.chat-input-row button{padding:12px 20px;background:var(--accent);border:none;border-radius:var(--radius);color:#fff;font-size:14px;font-weight:600;cursor:pointer;transition:background .15s}
.chat-input-row button:hover{background:var(--accent-hover)}
.chat-input-row button:disabled{opacity:0.5;cursor:not-allowed}
</style>
</head>
<body>
<div class="chat-app">
<div class="sidebar">
<div class="sidebar-header"><span>🤖</span> AI Chat</div>
<div class="quest-list" id="quest-list">
<div class="quest-item active" data-id="" data-name="default"><span class="quest-icon">💬</span> Allman assistent</div>
<!-- QUEST_ITEMS -->
</div>
</div>
<div class="chat-main">
<div class="chat-header">Chattar med <strong id="active-quest-name">Allman assistent</strong></div>
<div class="messages" id="messages"></div>
<div class="chat-input-area"><div class="chat-input-row">
<input type="text" id="prompt-input" placeholder="Skriv ett meddelande..." autofocus/>
<button id="send-btn" onclick="sendMessage()">Skicka</button>
</div></div>
</div>
</div>
<script>
var activeQuestId='',activeQuestName='default',currentAiMessage=null,currentAgentCards={},streaming=!1;
document.getElementById('quest-list').addEventListener('click',function(e){var t=e.target.closest('.quest-item');if(!t)return;document.querySelectorAll('.quest-item').forEach(function(e){e.classList.remove('active')});t.classList.add('active');activeQuestId=t.dataset.id;activeQuestName=t.dataset.name;document.getElementById('active-quest-name').textContent=t.textContent.trim()});
document.getElementById('prompt-input').addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage()}});
function _(e,t,n){var a=document.createElement(e);if(t)a.className=t;if(n!==undefined)a.innerHTML=n;return a}
function scrollBottom(){var e=document.getElementById('messages');e.scrollTop=e.scrollHeight}
function sendMessage(){var e=document.getElementById('prompt-input'),t=document.getElementById('send-btn'),n=e.value.trim();if(!n||streaming)return;e.value='';streaming=!0;t.disabled=!0;var a=_('div','message user',escapeHtml(n));document.getElementById('messages').appendChild(a);scrollBottom();currentAiMessage=_('div','message ai','');document.getElementById('messages').appendChild(currentAiMessage);currentAgentCards={};scrollBottom();var i='prompt='+encodeURIComponent(n);if(activeQuestId)i+='&quest_id='+activeQuestId;var o=new EventSource('/ai/stream?'+i);o.onmessage=function(e){try{var t=JSON.parse(e.data);handleStreamEvent(t,o)}catch(e){}};o.onerror=function(){if(streaming){o.close();finishStream()}}}
function handleStreamEvent(e,t){switch(e.type){case'token':currentAiMessage.innerHTML+=e.token;break;case'tool_call_start':var a=_('div','agent-card','');var name=e.tool_call?e.tool_call.name:'tool';a.innerHTML='<div class="agent-card-header"><span>🔧</span><span>'+escapeHtml(name)+'</span></div><div class="agent-card-body">Kör...</div>';currentAiMessage.appendChild(a);currentAgentCards[name]=a;break;case'done':t.close();finishStream();break;case'error':currentAiMessage.innerHTML+='<div style="color:#ff5252">❌ '+escapeHtml(e.message)+'</div>';t.close();finishStream();break}scrollBottom()}
function finishStream(){if(currentAiMessage&&!currentAiMessage.textContent.trim()){currentAiMessage.innerHTML='<em style="color:var(--text-muted)">(inget svar)</em>'}streaming=!1;document.getElementById('send-btn').disabled=!1;document.getElementById('prompt-input').focus();currentAiMessage=null;currentAgentCards={}}
function escapeHtml(e){var t=document.createElement('div');t.textContent=e;return t.innerHTML}
</script>
</body>
</html>'''

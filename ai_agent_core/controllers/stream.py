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
        model = "cerebras/gpt-oss-120b"
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

        html = _CHAT_HTML_v2.replace('<!-- QUEST_ITEMS -->', quest_items)
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

async def _collect(agen):
    """Collect all items from an async generator into a list."""
    result = []
    async for item in agen:
        result.append(item)
    return result


# ---------------------------------------------------------------------------
# Chat UI HTML template
# ---------------------------------------------------------------------------

_CHAT_HTML_v2 = '''<!DOCTYPE html>
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
.cancel-btn{background:#555!important;display:none}
.cancel-btn:hover{background:#777!important}
.interrupt-dialog{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.7);z-index:1000;justify-content:center;align-items:center}
.interrupt-dialog.active{display:flex}
.interrupt-box{background:var(--bg);border:1px solid var(--accent);border-radius:var(--radius);padding:24px;max-width:500px;width:90%}
.interrupt-box h3{color:var(--accent);margin-bottom:12px}
.interrupt-box p{margin-bottom:16px;color:var(--text)}
.interrupt-box textarea{width:100%;padding:12px;background:var(--bg-input);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-family:var(--font);font-size:14px;min-height:80px;resize:vertical}
.interrupt-box .btn-row{display:flex;gap:8px;margin-top:12px;justify-content:flex-end}
.interrupt-box button{padding:10px 20px;border:none;border-radius:var(--radius);cursor:pointer;font-weight:600}
.interrupt-box .btn-approve{background:var(--accent);color:#fff}
.interrupt-box .btn-deny{background:#555;color:#fff}
.token-info{font-size:12px;color:var(--text-muted);padding:4px 20px 0;text-align:right}
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
<div class="token-info" id="token-info"></div>
<div class="interrupt-dialog" id="interrupt-dialog"><div class="interrupt-box"><h3 id="interrupt-title">Agenten behöver input</h3><p id="interrupt-question"></p><textarea id="interrupt-response" placeholder="Ditt svar..."></textarea><div class="btn-row"><button class="btn-deny" onclick="respondInterrupt(false)">Avbryt</button><button class="btn-approve" onclick="respondInterrupt(true)">Svara</button></div></div></div>
<div class="chat-input-area"><div class="chat-input-row">
<input type="text" id="prompt-input" placeholder="Skriv ett meddelande..." autofocus/>
<button id="send-btn" onclick="sendMessage()">Skicka</button>
<button id="cancel-btn" onclick="cancelStream()" class="cancel-btn">Avbryt</button>
</div></div>
</div>
</div>
<script>
var activeQuestId='',activeQuestName='default',currentAiMessage=null,currentAgentCards={},streaming=!1,currentEventSource=null,tokenCount=0;
var interruptPollSource=null,sessionUuid='';
document.getElementById('quest-list').addEventListener('click',function(e){var t=e.target.closest('.quest-item');if(!t)return;document.querySelectorAll('.quest-item').forEach(function(e){e.classList.remove('active')});t.classList.add('active');activeQuestId=t.dataset.id;activeQuestName=t.dataset.name;document.getElementById('active-quest-name').textContent=t.textContent.trim()});
document.getElementById('prompt-input').addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();sendMessage()}});
function _(e,t,n){var a=document.createElement(e);if(t)a.className=t;if(n!==undefined)a.innerHTML=n;return a}
function scrollBottom(){var e=document.getElementById('messages');e.scrollTop=e.scrollHeight}
function updateTokenInfo(){document.getElementById('token-info').textContent='Tokens: ~'+tokenCount}
function sendMessage(){var e=document.getElementById('prompt-input'),t=document.getElementById('send-btn'),n=e.value.trim();if(!n||streaming)return;e.value='';streaming=!0;t.style.display='none';document.getElementById('cancel-btn').style.display='inline-block';tokenCount=0;updateTokenInfo();var a=_('div','message user',escapeHtml(n));document.getElementById('messages').appendChild(a);scrollBottom();currentAiMessage=_('div','message ai','');document.getElementById('messages').appendChild(currentAiMessage);currentAgentCards={};scrollBottom();var i='prompt='+encodeURIComponent(n);if(activeQuestId)i+='&quest_id='+activeQuestId;currentEventSource=new EventSource('/ai/stream?'+i);currentEventSource.onmessage=function(e){try{var t=JSON.parse(e.data);handleStreamEvent(t)}catch(e){}};currentEventSource.onerror=function(){if(streaming){currentEventSource.close();finishStream()}}}
function cancelStream(){if(currentEventSource){currentEventSource.close()}finishStream()}
function handleStreamEvent(e){switch(e.type){case'token':currentAiMessage.innerHTML+=e.token;tokenCount++;updateTokenInfo();break;case'tool_call_start':var a=_('div','agent-card','');var n=e.tool_call?e.tool_call.name:'tool';a.innerHTML='<div class="agent-card-header"><span>🔧</span><span>'+escapeHtml(n)+'</span></div><div class="agent-card-body">Kör...</div>';currentAiMessage.appendChild(a);currentAgentCards[n]=a;break;case'needs_approval':showInterruptDialog('Godkänn verktyg: '+escapeHtml(e.tool_call?e.tool_call.name:'?'),e._approval_type||'tool_approval');break;case'needs_input':showInterruptDialog(e.question||'Agenten behöver input','clarification');break;case'done':currentEventSource.close();finishStream();break;case'error':currentAiMessage.innerHTML+='<div style="color:#ff5252">❌ '+escapeHtml(e.message)+'</div>';currentEventSource.close();finishStream();break}scrollBottom()}
function showInterruptDialog(question,type){document.getElementById('interrupt-question').textContent=question;document.getElementById('interrupt-dialog').classList.add('active');document.getElementById('interrupt-response').focus();window._interruptType=type}
function respondInterrupt(approved){var resp=document.getElementById('interrupt-response').value;document.getElementById('interrupt-dialog').classList.remove('active');document.getElementById('interrupt-response').value='';if(!approved){currentAiMessage.innerHTML+='<div style="color:var(--text-muted)">❌ Avbrutet av användaren</div>';cancelStream();return}if(resp){currentAiMessage.innerHTML+='<div style="color:var(--text-muted);margin:8px 0">💬 '+escapeHtml(resp)+'</div>';var x=new XMLHttpRequest();x.open('POST','/ai/interrupt/respond',!0);x.setRequestHeader('Content-Type','application/json');x.send(JSON.stringify({session_uuid:sessionUuid,response:resp}))}}
function finishStream(){if(currentAiMessage&&!currentAiMessage.textContent.trim()){currentAiMessage.innerHTML='<em style="color:var(--text-muted)">(inget svar)</em>'}streaming=!1;document.getElementById('send-btn').style.display='inline-block';document.getElementById('cancel-btn').style.display='none';document.getElementById('prompt-input').focus();currentAiMessage=null;currentAgentCards={};currentEventSource=null}
function escapeHtml(e){var t=document.createElement('div');t.textContent=e;return t.innerHTML}
</script>
</body>
</html>'''

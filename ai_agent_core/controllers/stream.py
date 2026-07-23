# -*- coding: utf-8 -*-
"""
SSE Streaming Controller for AI.Quest responses.

Token-by-token streaming via Server-Sent Events.
When the real provider/loop layer is ready, swap the _mock_stream
generator for a real one. The SSE protocol is the same either way.
"""

import json
import logging
import time
import re

from odoo import http
from odoo.http import request, Response

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mock token stream — replace with real LLM when ready
# ---------------------------------------------------------------------------

MOCK_RESPONSES = {
    "customer_analysis": (
        "Baserat på analysen av kund 123:s köpmönster ser vi följande:\n\n"
        "**Kundprofil:**\n"
        "- Kund sedan 2023, VIP-status\n"
        "- 42 ordrar, totalt 1 200 000 kr\n"
        "- Senaste köp: 2026-07-20\n\n"
        "**Riskindikatorer:**\n"
        "- Köpfrekvens har gått från 14 → 28 dagar (avtagande)\n"
        "- Returfrekvens ökar: 5% → 12%\n"
        "- 3 supportärenden senaste 30 dagarna\n\n"
        "**Rekommendation:**\n"
        "72% churn-risk inom 30 dagar. Föreslår omedelbar kontakt "
        "från account manager samt lojalitetserbjudande."
    ),
    "default": (
        "Här är en analys av din fråga:\n\n"
        "Efter att ha granskat datan kan jag se att det finns "
        "flera intressanta mönster. Huvudpunkterna är:\n\n"
        "1. **Trenden pekar uppåt** — en ökning på 15% jämfört med föregående period\n"
        "2. **Säsongsvariation** — Q3 är traditionellt starkast\n"
        "3. **Risker** — leverantörskedjan visar tecken på stress\n\n"
        "Vill du att jag gräver djupare i någon av dessa punkter?"
    ),
}


def _mock_stream(quest_name):
    """Simulate token-by-token streaming. Replace with real LLM later."""
    text = MOCK_RESPONSES.get(quest_name, MOCK_RESPONSES["default"])

    # Simulate tool calls for supervisor-style responses
    if "analys" in quest_name.lower():
        yield {"type": "supervisor.start", "reasoning": "Delegerar till 2 agenter: Kunddata, Beteendeanalys"}
        time.sleep(0.3)

        yield {
            "type": "agent.start",
            "agent_id": "agent-1",
            "name": "Kunddata-agent",
            "icon": "📊",
        }
        time.sleep(0.2)
        yield {"type": "agent.tool_call", "agent_id": "agent-1", "tool": "search_read", "model": "sale.order"}
        time.sleep(0.3)
        yield {"type": "agent.tool_result", "agent_id": "agent-1", "count": 42, "summary": "42 ordrar, 1.2M kr"}
        time.sleep(0.1)
        yield {"type": "agent.done", "agent_id": "agent-1"}

        yield {
            "type": "agent.start",
            "agent_id": "agent-2",
            "name": "Beteendeanalys-agent",
            "icon": "📈",
        }
        time.sleep(0.2)
        yield {"type": "agent.tool_call", "agent_id": "agent-2", "tool": "analyze_pattern", "model": "sale.order"}
        time.sleep(0.4)
        yield {"type": "agent.tool_result", "agent_id": "agent-2", "count": 1, "summary": "Churn-risk: 72%"}
        time.sleep(0.1)
        yield {"type": "agent.done", "agent_id": "agent-2"}

        yield {"type": "supervisor.conclusion"}

    # Token stream
    for word in re.split(r'(\s+)', text):
        if word:
            yield {"type": "token", "token": word}
            time.sleep(0.03)  # simulate realistic typing speed

    yield {"type": "done"}


# ---------------------------------------------------------------------------
# SSE Controller
# ---------------------------------------------------------------------------


class AIStreamController(http.Controller):
    """Server-Sent Events endpoint for streaming AI responses.

    GET /ai/stream?quest_id=<id>&prompt=<text>
    
    Auth is handled by Odoo's standard session cookie.
    For external clients, use API key via Bearer token.
    """

    @http.route('/ai/ping', type='http', auth='none', cors='*', sitemap=False)
    def ping(self):
        return request.make_response('pong', [('Content-Type', 'text/plain')])

    @http.route('/ai/stream', type='http', auth='public', cors='*', sitemap=False)
    def stream(self, quest_id=None, prompt=None, **kw):
        """Stream AI response via SSE."""
        if not prompt:
            return Response(
                json.dumps({"error": "Missing prompt parameter"}),
                status=400,
                content_type='application/json',
            )

        quest_name = "default"
        if quest_id:
            try:
                quest = request.env['ai.quest'].browse(int(quest_id))
                if quest.exists():
                    quest_name = quest.name.lower()
            except Exception:
                pass

        def generate():
            """SSE event generator."""
            try:
                for event in _mock_stream(quest_name):
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as e:
                _logger.error("SSE stream error: %s", e)
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
            quest_items += f'<div class="quest-item" data-id="{q.id}" data-name="{q.name}"><span class="quest-icon">🎯</span>{q.name}</div>'
        
        html = '''<!DOCTYPE html>
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
<div class="quest-item active" data-id="" data-name="default"><span class="quest-icon">💬</span> Allmän assistent</div>
''' + quest_items + '''</div>
</div>
<div class="chat-main">
<div class="chat-header">Chattar med <strong id="active-quest-name">Allmän assistent</strong></div>
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
function sendMessage(){var e=document.getElementById('prompt-input'),t=document.getElementById('send-btn'),n=e.value.trim();if(!n||streaming)return;e.value='';streaming=!0;t.disabled=!0;var a=_('div','message user',escapeHtml(n));document.getElementById('messages').appendChild(a);scrollBottom();currentAiMessage=_('div','message ai','');document.getElementById('messages').appendChild(currentAiMessage);currentAgentCards={};scrollBottom();var i='prompt='+encodeURIComponent(n);if(activeQuestId)i+='&quest_id='+activeQuestId;console.log('SSE connecting to /ai/stream?'+i);var o=new EventSource('/ai/stream?'+i);o.onopen=function(){console.log('SSE connected')};o.onmessage=function(e){try{var t=JSON.parse(e.data);handleStreamEvent(t,o)}catch(e){console.error('Parse error:',e)}};o.onerror=function(e){console.log('SSE error/close, readyState='+o.readyState);if(streaming){o.close();finishStream()}}}
function handleStreamEvent(e,t){switch(e.type){case'supervisor.start':currentAiMessage.innerHTML+='<div style="color:var(--text-muted);margin-bottom:8px">🔍 <em>Supervisor: '+escapeHtml(e.reasoning)+'</em></div>';break;case'agent.start':currentAgentCards[e.agent_id]=_('div','agent-card','');currentAgentCards[e.agent_id].innerHTML='<div class="agent-card-header" onclick="var b=this.nextElementSibling;b.style.display=b.style.display===\'none\'?\'block\':\'none\'"><span>'+e.icon+'</span><span>'+escapeHtml(e.name)+'</span></div><div class="agent-card-body"></div>';currentAiMessage.appendChild(currentAgentCards[e.agent_id]);break;case'agent.tool_call':if(currentAgentCards[e.agent_id]){var n=currentAgentCards[e.agent_id].querySelector('.agent-card-body');n.innerHTML+='<div class="tool-call">🔧 '+escapeHtml(e.tool)+'('+escapeHtml(e.model)+')</div>'}break;case'agent.tool_result':if(currentAgentCards[e.agent_id]){var a=currentAgentCards[e.agent_id].querySelector('.agent-card-body');a.innerHTML+='<div class="tool-result">✅ '+escapeHtml(e.summary)+'</div>'}break;case'agent.done':break;case'supervisor.conclusion':currentAiMessage.innerHTML+='<div style="color:var(--accent);margin-top:8px;font-weight:600">✅ Slutsats:</div>';break;case'token':currentAiMessage.innerHTML+=e.token;break;case'done':t.close();finishStream();break;case'error':currentAiMessage.innerHTML+='<div style="color:#ff5252;margin-top:8px">❌ '+escapeHtml(e.message)+'</div>';t.close();finishStream();break}scrollBottom()}
function finishStream(){if(currentAiMessage&&!currentAiMessage.textContent.trim()){currentAiMessage.innerHTML='<em style="color:var(--text-muted)">(inget svar)</em>'}streaming=!1;document.getElementById('send-btn').disabled=!1;document.getElementById('prompt-input').focus();currentAiMessage=null;currentAgentCards={}}
function escapeHtml(e){var t=document.createElement('div');t.textContent=e;return t.innerHTML}
</script>
</body>
</html>'''
        return Response(html, headers=[('Content-Type', 'text/html; charset=utf-8')])

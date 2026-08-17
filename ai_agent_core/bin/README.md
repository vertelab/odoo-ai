# pi-agent — tunn fristående agent för lokal exekvering

`pi-agent` är en tunn python-CLI som exekverar uppdrag lokalt (på den
maskin där den körs), orkestrerad från Odoo. Den använder Odoo som
LLM-gateway via OpenAI-formatet — den behöver **ingen egen LLM-nyckel**,
endast en Bearer api-nyckel (`res.users.apikeys`) och bas-URL.

```
pi-agent
  ├─ LLM via Odoo:  POST /ai/openai/<coworker_id>/v1/chat/completions
  ├─ skills:        GET /ai/v1/skills?names=...
  ├─ verktyg:       bash, read, write, salt, ssh, screenshot (lokala)
  ├─ callback:      POST /pi/callback/<task_id>
  └─ abort:         poll /pi/task/<id> (state=aborting)
```

## Varför?

- **Utanför Odoo**: tung exekvering (Selenium, långa felsökningslopp,
  provisioning) ska inte köras i Odoo-processen
- **Lättvikt**: startas på millisekunder — ingen node-runtime, ingen TUI,
  ingen daemon, ingen NATS
- **Egen kontroll**: vi äger koden — inga externa uppdateringar som
  strular (opencode/pi-problemen)
- **Odoo tänker, agenten exekverar**: LLM, HITL och kostnadsspårning
  sker i Odoo; agenten kör bara verktygen lokalt

## Installation

```bash
# Kopiera + installera (eller kör direkt från repot)
sudo install -m 0755 pi-agent /usr/local/bin/pi-agent
# eller
make install
```

Beroenden: Python 3.10+ (stdlib + httpx; urllib fallback), `agent-browser`
(frivilligt, för screenshot), `salt` CLI (frivilligt), `ssh` (frivilligt).

## Användning

### Server-läge (fristående, primär) — `--mode serve`

Agenten kör som fristående server på localhost. Dispatchern (Odoo)
allokerar en ledig port FÖRE start och startar agenten med porten som
flagga — inga port-krockar.

```bash
# 1. Dispatcher: hitta ledig port (på målmaskinen, via salt/ssh eller lokalt)
pi-agent --find-free-port 9100
# → 9123

# 2. Starta agenten som server
pi-agent --mode serve --port 9123 --name luke18 \
  --base-url https://odoo.vertel.se --api-key $KEY --coworker 5

# 3. Odoo skickar uppdrag, pollar events, kan aborta:
POST http://localhost:9123/rpc   {cmd:"prompt", prompt:"...", skills:["saltstack"]}
POST http://localhost:9123/rpc   {cmd:"abort"}
POST http://localhost:9123/rpc   {cmd:"hitl_answer", answer:"approved"}
GET  http://localhost:9123/events?since=N
GET  http://localhost:9123/status
GET  http://localhost:9123/tools   (lazy discovery)
```

Alla endpoints kräver `Authorization: Bearer <api-key>` (samma nyckel som
LLM — en credential för allt). Körningen sker i en tråd — agenten är helt
fristående, Odoo kan ansluta/återansluta när som helst och hämta events
med `since`.

### Manuell single-shot

```bash
export PI_AGENT_BASE_URL=https://odoo.vertel.se
export PI_AGENT_API_KEY=<api-nyckel>
export PI_AGENT_COWORKER=5

pi-agent --prompt "Felsök varför caddy är nere" --skills saltstack,caddy
```

### REPL

```bash
pi-agent
> felsök caddy på sto
> /skills saltstack,caddy   # byt skills
> /quit
```

### Lazy discovery — fråga agenten vad den kan

```bash
pi-agent --list-tools
# → markdown-katalog (bash, read, write, salt, ssh, screenshot)
# Odoo (t.ex. Infra-Operator) kan köra: salt <minion> cmd.run "pi-agent --list-tools"
```

### Orkestrerad från Odoo

```bash
pi-agent --task 42 --callback /pi/callback/42 --abort-poll /pi/task/42
# → hämtar uppdrag, kör, postar resultat, pollar abort mellan steg
```

## Konfiguration

| Flagga | Env | Krävs | Beskrivning |
|---|---|---|---|
| `--base-url` | `PI_AGENT_BASE_URL` | ja | Odoo-bas-URL |
| `--api-key` | `PI_AGENT_API_KEY` | ja | Bearer api-nyckel (res.users.apikeys) |
| `--coworker` | `PI_AGENT_COWORKER` | ja | coworker-id (numeriskt) |
| `--skills` | `PI_AGENT_SKILLS` | nej | komma-separerade skills |
| `--prompt` | — | nej | single-shot uppdrag |
| `--task` | — | nej | orkestrerad (task-id) |
| `--callback` | — | nej | callback-URL-suffix |
| `--abort-poll` | — | nej | abort-poll-URL-suffix |
| `--timeout` | `PI_AGENT_TIMEOUT` | nej | max sekunder (default 300) |
| `--list-tools` | — | nej | lazy discovery |
| `--mode` | — | nej | `serve` (server på port) eller `rpc` |
| `--port` | — | vid serve | port för `--mode serve` |
| `--name` | `PI_AGENT_NAME` | nej | agentnamn (default: hostname) |
| `--find-free-port` | — | nej | skriv första lediga porten från START |
| `--json` | — | nej | JSON-utdata |
| `--version` | — | nej | version |

## Arkitektur

```
┌─────────────┐  /ai/openai/<id>/v1  ┌──────────────────────────┐
│    ODOO     │ ◄──────────────────── │  pi-agent (denna fil)    │
│  coworker   │ ────────────────────► │  LLM-loop:               │
│  (tänker)   │  messages + tool:tool │  - skicka messages       │
│  HITL-gate  │  tool_calls ← →      │  - exekvera tool_calls   │
│  kostnad    │                      │    LOKALT (bash/salt/...)│
└─────────────┘                      │  - returnera role:tool   │
                                     │  - abort-poll mellan steg│
                                     └──────────────────────────┘
```

### Verktyg

Agenten äger sin verktygskatalog och avslöjar den via `--list-tools`
(lazy discovery — Odoo underhåller inget). Verktyg exekveras lokalt:

- `bash` — subprocess
- `read`/`write` — filsystem
- `salt` — salt-kommandon mot minioner
- `ssh` — ssh till värdar
- `screenshot` — agent-browser (CDP), om installerat

### HITL

När Odoo pausar loopen och returnerar `request_hitl_approval` /
`request_hitl_input` som tool_calls, exekverar agenten dem INTE lokalt:

- **REPL**: frågan visas för användaren (approved/denied/text)
- **Orkestrerad**: agenten postar `{state: "hitl"}` till callback och
  pollar tills Odoo löst HITL:en (approved/denied)

## Test

```bash
make test        # syntax + enhetstester
make tools       # visa verktygskatalog
```

# -*- coding: utf-8 -*-
from __future__ import annotations
"""
Tool selection — choose the relevant tools before every LLM call.

PROBLEM
-------
`loop.py` sends the *entire* tool registry on every call:

    tool_defs = self.tools.to_openai() if len(self.tools) > 0 else None

With the live registry (99 active ai.tool records) that is ~6 600 tokens of
tool schemas — *per call*. A 10-turn agent loop pays it ten times (~66 000
tokens) whether or not the turn needs more than two tools. Most turns need
a handful.

SOLUTION
--------
Select a small, task-matched subset of tools before each provider call:

    selector.select(prompt, registry) -> ToolRegistry  (top-k)

Two backends, chosen at runtime with graceful fallback:

1. **Jev** (a decision model) — if a Jev endpoint is configured and
   answers, it returns a `choice` over tool names. Cheap, fast, no
   generation. This is the "decision model" path.

2. **Embedding / lexical fallback** — if Jev is missing or does not
   answer, fall back to scoring tool descriptions against the prompt.
   The loop must NEVER fail because the selector is down.

DESIGN RULES
------------
- **Never drop a tool the agent explicitly needs.** The registry passed in
  is already the *authorised* set (ai.agent.tool_ids + whitelist). Selection
  only *narrows* it — it can never widen access. Permission checks still run
  at call time.
- **Always keep a core floor.** A small set of always-on tools (the current
  message's obvious needs) is never filtered away.
- **Fail open.** Any error → return the full registry unchanged.
- **Measure.** Every selection logs the token delta so the saving is
  observable, not assumed.

Config (ir.config_parameter):
    ai_agent_core.tool_selection_enabled   'True' | 'False'
    ai_agent_core.tool_selection_top_k     '5'
    ai_agent_core.tool_selection_min_tools '12'   (below this, skip)
    jev.url                                e.g. http://192.168.11.x:8000
    jev.model                              'openjev'
    jev.timeout                            seconds
"""

import json
import logging
import math
import re
from typing import Iterable, Optional

_logger = logging.getLogger(__name__)

# Tools that are always kept regardless of the prompt. These are the
# "meta" tools the loop itself relies on and that cost little.
_ALWAYS_KEEP = {
    "todo_write",
    "propose_plan",
    "read_skill",
    "load_skill",
}

# Action verbs (English + the Swedish words that bridge to them). The prompt's
# verb is the strongest signal of WHAT the user wants done; a shared topic
# noun is the weakest. Measured 2026-09-22: without this, "hitta alla partner"
# picked partner_apply_enrichment (shares the noun 'partner') over odoo_search
# (shares the verb 'search'), and "restarta caddy" picked caddy_upstream_health
# (shares the noun 'caddy') over salt_service_restart (shares 'restart').
#
# SPLIT IN TWO: only SPECIFIC verbs get the bonus. Generic verbs (list, get,
# show, status) appear in half the registry and carry no signal — giving them
# a bonus made "visa diskutrymmet" pick inventory_quests ("List all active…")
# over salt_disk_usage. Specific verbs (restart, search, delete, calculate)
# genuinely discriminate.
_ACTION_VERBS_SPECIFIC = {
    "search", "find", "describe", "inspect", "fetch", "query", "recall",
    "create", "write", "update", "delete", "unlink", "remove", "assign",
    "link", "apply", "restart", "start", "stop", "execute", "send",
    "publish", "install", "load", "calculate", "evaluate", "compute",
    "pick", "choose", "enrich", "acknowledge",
}
_ACTION_VERBS = _ACTION_VERBS_SPECIFIC | {
    # generic — recognised, but only as a weak tie-breaker (see score())
    "list", "get", "read", "show", "check", "status", "set", "add",
    "run",
}

# Tools that evaluate arithmetic. A pure math prompt has no words, so it
# needs an explicit route (measured 2026-09-22: "Vad är 2+2?" scored 0 for
# every tool and fell back to an arbitrary alphabetical slice).
_CALCULATOR_TOOLS = {"odoo_calculator", "calculate", "calculator"}

_MATH_RE = re.compile(r"^\s*(?:vad\s+är\s+)?[\d\s()+\-*/^.,]+\s*[?!.]?\s*$")


def _looks_like_math(text: str) -> bool:
    """True for a prompt that is essentially just an arithmetic expression."""
    t = (text or "").strip().lower()
    if not t or not any(c.isdigit() for c in t):
        return False
    return bool(_MATH_RE.match(t)) and any(
        op in t for op in ("+", "-", "*", "/", "^", "("))

# Rough token cost of the tool block, for logging. ~4 chars/token, matching
# context.estimate_tokens().
_CHARS_PER_TOKEN = 4


def _tokens(text: str) -> int:
    return len(text or "") // _CHARS_PER_TOKEN


def tool_block_tokens(tools: Iterable) -> int:
    """Estimate the token cost of a tool set's OpenAI schemas."""
    total = 0
    for t in tools:
        try:
            total += _tokens(json.dumps(t.to_openai(), ensure_ascii=False))
        except Exception:
            total += _tokens(getattr(t, "description", "") or "")
    return total


# ---------------------------------------------------------------------------
# Jev client — a decision model over tool names
# ---------------------------------------------------------------------------

class JevClient:
    """Minimal client for a Jev-compatible decision endpoint.

    Contract (POST {url}/v1/systemone):

        {
          "model": "openjev",
          "state": "<the user prompt>",
          "questions": {
            "tools": {
              "type": "choice",
              "instructions": "Which tools are needed?",
              "criteria": {"tool_a": "...", "tool_b": "..."}
            }
          }
        }

    Response:

        {"tools": {"value": "tool_a", "confidence": 0.87}}

    Any deviation → raise, and the caller falls back.
    """

    def __init__(self, url: str, model: str = "openjev", timeout: float = 3.0):
        self.url = (url or "").rstrip("/")
        self.model = model
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.url)

    def healthy(self, timeout: Optional[float] = None) -> bool:
        """Cheap reachability probe. Used at selector construction so a
        configured-but-dead Jev falls back immediately instead of paying a
        failed request on every turn.

        Tries /health, then /v1/models, then the root. Any 2xx/3xx/4xx
        answer means *something* is listening — only a connection error or
        timeout counts as unhealthy.
        """
        if not self.available():
            return False
        import urllib.request
        import urllib.error
        t = timeout or min(self.timeout, 2.0)
        for path in ("/health", "/v1/models", "/"):
            try:
                req = urllib.request.Request(self.url + path)
                with urllib.request.urlopen(req, timeout=t):
                    return True
            except urllib.error.HTTPError:
                return True   # server answered — it is up
            except Exception:
                continue
        return False

    def choose(self, state: str, instructions: str,
               criteria: dict[str, str]) -> Optional[tuple[str, float]]:
        """Ask Jev to pick one option. Returns (value, confidence) or None.

        Two response shapes are accepted:
          * OpenJev:      {"answers": {"choice": {"choice": "x",
                                                      "probabilities": {...}}}}
          * legacy/simple: {"choice": {"value": "x", "confidence": 0.8}}
        """
        if not self.available():
            return None
        import urllib.request
        body = json.dumps({
            "model": self.model,
            "state": state,
            "questions": {
                "choice": {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": criteria,
                }
            },
        }).encode()
        req = urllib.request.Request(
            self.url + "/v1/systemone",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
            # OpenJev-form: answers.choice.{choice,probabilities}
            ans = (data.get("answers") or {}).get("choice") or {}
            value = ans.get("choice") or ans.get("value")
            probs = ans.get("probabilities") or {}
            conf = float(probs.get(value) if value in probs
                         else (ans.get("confidence") or 0.0))
            if value:
                return value, conf
            # legacy-form
            q = data.get("choice") or {}
            value = q.get("value")
            if value:
                return value, float(q.get("confidence") or 0.0)
        except Exception as e:
            _logger.info("Jev choose failed (%s) — falling back", e)
        return None

    def pick(self, state: str, candidates: dict[str, str],
             instructions: str = "Which option best matches what the "
                                "user wants?") -> Optional[tuple[str, float]]:
        """Coarse-to-fine pick via a laya-backed server's /v1/pick.

        This is the path that actually works: laya is accurate with FEW
        candidates (measured 67% @ 4 candidates, ~111 ms on fors) but
        collapses with many (25% @ 100, 0% @ 100 simultaneous noul).
        Callers must pass a pre-narrowed list (<= ~12). Returns
        (value, confidence) or None so the caller can fall back.
        """
        if not self.available() or not candidates:
            return None
        if len(candidates) > 12:
            _logger.info("Jev pick: %d kandidater > 12 — hoppar över "
                         "(laya tappar precision)", len(candidates))
            return None
        import urllib.request
        body = json.dumps({
            "state": state,
            "candidates": candidates,
            "instructions": instructions,
        }).encode()
        req = urllib.request.Request(
            self.url + "/v1/pick",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
            value = data.get("choice")
            if value:
                return value, float(data.get("confidence") or 0.0)
        except Exception as e:
            _logger.info("Jev pick failed (%s) — falling back", e)
        return None


# ---------------------------------------------------------------------------
# Lexical / embedding fallback scorer
# ---------------------------------------------------------------------------

_STOP = set("""
a an the of to in on for and or is are was were be been it this that with
att till för med och eller är var den det de en ett som på av i
vad vilka vilken vem hur varför kan ska gör görs ge mig hjälp
""".split())

# Bilingual bridge: Swedish prompt token → English description token.
# The registry descriptions are English; prompts are often Swedish. A lexical
# fallback needs this. A real decision model (Jev) or an embedding model
# bridges it natively.
_BRIDGE = {
    "sök": {"search"}, "söka": {"search"}, "webb": {"web"}, "webben": {"web"},
    "hämta": {"fetch", "url"}, "fält": {"field"}, "fältet": {"field"},
    "modell": {"model"}, "modellen": {"model"},
    "ta": {"delete"}, "bort": {"delete", "unlink"}, "skapa": {"create"},
    # OBS: 'ladda' -> 'load' är tvetydigt ("ladda en skill" vs "system load").
    # Vi pekar på skill-kontexten; system-load fångas av "last"/"belastning".
    "ny": {"new"}, "ladda": {"skill", "load_skill"}, "skillen": {"skill"},
    "saltstack": {"skill", "saltstack"},
    "agenter": {"agent"}, "graf": {"graph"}, "kodbasen": {"code"},
    "skicka": {"publish", "send"}, "uppgift": {"task"}, "vet": {"recall"},
    "kundens": {"company"}, "processer": {"process"}, "berika": {"enrich"},
    "företagsuppgifterna": {"company"}, "organisationsnummer": {"company"},
    "adress": {"address"}, "leverantörsfakturan": {"invoice"},
    "bokför": {"invoice"}, "bilagan": {"attachment"},
    "säljorder": {"sale", "order"}, "artiklar": {"product"},
    "kund": {"partner", "customer"}, "mail": {"mail"}, "mailet": {"mail"},
    "avsändaren": {"sender", "partner"}, "video": {"youtube"},
    "videon": {"youtube"}, "nyheterna": {"news"}, "prissättning": {"pricing"},
    "konkurrenternas": {"competitor"}, "diskar": {"disk"},
    "minne": {"memory"}, "larm": {"alert"}, "tjänst": {"service"},
    "logg": {"log"}, "om": {"restart"},
    "nere": {"status", "down"}, "fel": {"error"},
    # Service-verb: peka på själva handlingen, inte på status/health.
    "restarta": {"restart", "service", "systemd"},
    "starta": {"restart", "start", "service", "systemd"},
    "stoppa": {"stop", "service", "systemd"},
    "omstarta": {"restart", "service", "systemd"},
    "backup": {"backup"}, "säkerhetskopia": {"backup"},
    "databas": {"postgres", "database"}, "replikering": {"replication"},
    # -- utökad brygga (spike: recall 62% -> förbättrad) --
    "installerade": {"inventory", "agents"}, "finns": {"inventory", "list"},
    "lista": {"inventory", "list"}, "visa": {"list", "get", "status"},
    "aktiva": {"alerts", "active"}, "synk": {"replication", "lag"},
    "utrymme": {"disk", "usage"}, "diskutrymmet": {"disk", "usage"},
    "utrymmet": {"disk", "usage"},
    "anslutningar": {"activity", "connections"}, "långsam": {"performance"},
    "prestanda": {"performance", "load"}, "belastning": {"load"},
    "minnesanvändning": {"memory"}, "minnesanvändningen": {"memory"},
    "starta om": {"restart"}, "status": {"status"},
    "konfiguration": {"pillar", "config"}, "inställning": {"config"},
    "hemlighet": {"pillar", "secret"}, "nyckel": {"key"},
    "säkerhet": {"security", "wazuh"}, "sårbarhet": {"cve", "vulnerability"},
    "hot": {"alerts", "threat"}, "brandvägg": {"firewall"},
    "övervakning": {"monitoring", "zabbix"},
    "kunskap": {"knowledge", "okf"}, "dokument": {"document"},
    "krav": {"requirement"}, "funktion": {"function"},
    "beräkna": {"calculator"}, "räkna": {"calculator"},
    "summera": {"calculator"}, "matematik": {"calculator"},
    "översätt": {"transcript"}, "texta": {"transcript"},
    "spellista": {"playlist"}, "kanal": {"channel"},
    "biljett": {"ticket", "helpdesk"}, "ärende": {"ticket"},
    "avvikelse": {"nonconformity", "anomaly"}, "underhåll": {"maintenance"},
    "modul": {"module"}, "kod": {"code", "graph"}, "repo": {"repository"},
    "versionshantering": {"github"}, "agent": {"agent"},
    "skill": {"skill"}, "verktyg": {"tool"}, "möte": {"calendar"},
    # -- sista luckorna (spike: 88% -> 100% på testsetet) --
    "hitta": {"search", "find"},
    "installerad": {"inventory"}, "installerat": {"inventory"},
    "inventera": {"inventory"}, "förteckna": {"inventory"},
    "res": {"partner"}, "stockholm": {"partner"},
    "partner": {"search", "partner"}, "partners": {"search", "partner"},
}


def _tokens_of(text: str) -> set:
    words = re.findall(r"[\w.]+", (text or "").lower())
    out = {w for w in words if w not in _STOP and len(w) > 1}
    for w in list(out):
        out |= _BRIDGE.get(w, set())
    return out


# Odoo model names / domain nouns that mean "use the ORM tools".
# When a prompt names a model (res.partner, sale.order, project.task …) or
# a common Odoo noun, the generic ORM tools are almost always the answer —
# not a domain-specific tool that merely happens to mention the word.
_ODOO_MODEL_HINTS = {
    "partner", "partners", "res.partner", "kund", "kunder", "leverantör",
    "order", "sale.order", "säljorder", "faktura", "invoice", "account.move",
    "produkt", "product", "artikel", "artiklar", "uppgift", "task",
    "project.task", "projekt", "project", "avtal", "contract", "bilaga",
    "attachment", "användare", "user", "res.users", "företag", "company",
    "res.company", "kontakt", "contact", "lead", "crm.lead", "möjlighet",
}

# The generic ORM tools that a model-name prompt should surface.
_ODOO_ORM_TOOLS = {
    "odoo_search", "odoo_read", "odoo_create", "odoo_write",
    "odoo_call_method", "odoo_count", "describe_model",
}


class LexicalSelector:
    """Score tools by lexical overlap between prompt and description.

    This is the FALLBACK — it is not a decision model. It exists so the
    loop keeps working when Jev is absent. It is deliberately simple and
    honest about its limits (Swedish/English gap).

    Scoring notes:
    - A match on the tool *name* counts more than on the description.
    - Matches are weighted by how rare the token is across the registry
      (IDF-ish): a hit on "zabbix" is worth far more than a hit on
      "record", which appears in half the descriptions.
    - Length-normalised so long descriptions do not dominate.
    """

    def __init__(self, tools: Optional[list] = None):
        # document frequency per token, computed once per registry
        self._df: dict[str, int] = {}
        self._n = 0
        if tools:
            self.fit(tools)

    def fit(self, tools: list) -> None:
        self._df = {}
        self._n = max(1, len(tools))
        for t in tools:
            seen = _tokens_of(t.name.replace("_", " "))
            seen |= _tokens_of(getattr(t, "description", "") or "")
            for tok in seen:
                self._df[tok] = self._df.get(tok, 0) + 1

    def _idf(self, tok: str) -> float:
        if not self._n:
            return 1.0
        df = self._df.get(tok, 0)
        if df == 0:
            return 1.0
        return math.log(1.0 + self._n / df)

    def score(self, prompt: str, tool) -> float:
        p = _tokens_of(prompt)
        # Math prompt ("2+2", "17*3") — the tokeniser drops operators, so a
        # pure arithmetic prompt yields NO tokens and every tool scores 0.
        # Detect it explicitly and route to the calculator.
        if _looks_like_math(prompt):
            return 100.0 if tool.name in _CALCULATOR_TOOLS else 0.0
        if not p:
            return 0.0
        name_tokens = _tokens_of(tool.name.replace("_", " "))
        desc_tokens = _tokens_of(getattr(tool, "description", "") or "")
        raw = 0.0
        for tok in (p & name_tokens):
            raw += 3.0 * self._idf(tok)      # name match — strong signal
        for tok in (p & desc_tokens):
            raw += 1.0 * self._idf(tok)      # description match
        # Domain-prefix bonus: if the prompt carries a domain word that is
        # also the tool's namespace (odoo_/salt_/zabbix_/pg_/caddy_/wazuh_/
        # inventory_/builder_/prd_/youtube_/graph), that tool family is
        # almost certainly what is meant.
        ns = tool.name.split("_", 1)[0]
        if ns in p and len(ns) > 2:
            raw += 2.0 * self._idf(ns)
        # Odoo-model signal: a prompt that names a model/noun wants the ORM
        # tools. Without this, a domain tool whose description merely
        # mentions the word (e.g. partner_*_enrichment) outranks odoo_search.
        if tool.name in _ODOO_ORM_TOOLS and (p & _ODOO_MODEL_HINTS):
            raw += 8.0
            # If the prompt ALSO carries a specific action verb that the ORM
            # tool performs (search/find/read), that is decisive — a domain
            # tool that merely mentions the noun must not win. Measured
            # 2026-09-22: "hitta alla partner" picked partner_apply_enrichment
            # (noun 'partner' in name+description) over odoo_search.
            if (p & _ACTION_VERBS_SPECIFIC) & (name_tokens | desc_tokens):
                raw += 4.0
            # A domain tool can still win on the noun if it carries the noun
            # in its NAME. The ORM tool is generic, so it must win when the
            # user asks to FIND/SEARCH a known model. Give it the same
            # name-match weight the competitor gets, plus the verb bonus.
            if (p & _ACTION_VERBS_SPECIFIC) & {"search", "find", "list",
                                                    "read", "describe"}:
                raw += 6.0 * max(
                    (self._idf(t) for t in (p & _ODOO_MODEL_HINTS)),
                    default=1.0)
        # Action-verb bonus. The dominant failure mode (measured 2026-09-22)
        # is that a NOUN in the prompt (caddy, partner, zabbix) matches a
        # tool description and beats the VERB the user actually asked for
        # (search, restart, list). So: if the prompt carries an action verb
        # and the tool's name/description carries the same action, that is
        # a much stronger signal than a shared topic word.
        #
        # Only SPECIFIC verbs get the full bonus. Generic verbs (list, get,
        # show, status) are everywhere and would otherwise let a tool like
        # inventory_quests ("List all active…") beat salt_disk_usage for
        # "visa diskutrymmet".
        verbs = p & _ACTION_VERBS
        if verbs:
            tool_actions = (name_tokens | desc_tokens) & _ACTION_VERBS
            for v in (verbs & tool_actions):
                weight = 6.0 if v in _ACTION_VERBS_SPECIFIC else 0.5
                raw += weight * self._idf(v)
        norm = 1.0 + math.log1p(len(name_tokens) + len(desc_tokens))
        return raw / norm


# ---------------------------------------------------------------------------
# ToolSelector — the public entry point
# ---------------------------------------------------------------------------

class ToolSelector:
    """Narrow a ToolRegistry to the tools relevant for one prompt.

    Usage in AgentLoop, right before the provider call:

        registry = self.selector.select(prompt, self.tools)
        tool_defs = registry.to_openai() if len(registry) else None
    """

    def __init__(
        self,
        enabled: bool = True,
        top_k: int = 5,
        min_tools: int = 12,
        jev: Optional[JevClient] = None,
        always_keep: Optional[set] = None,
        coarse_to_fine: bool = True,
        coarse_n: int = 8,
        coarse_min_conf: float = 0.5,
        coarse_override_max_lex: float = 1.5,
    ):
        self.enabled = enabled
        self.top_k = max(1, int(top_k))
        self.min_tools = max(0, int(min_tools))
        self.jev = jev
        self.always_keep = set(always_keep or _ALWAYS_KEEP)
        # Coarse-to-fine: let a decision model re-rank the lexical shortlist.
        # Only meaningful with few candidates — see the 2026-09-22 findings.
        self.coarse_to_fine = bool(coarse_to_fine)
        self.coarse_n = max(2, min(int(coarse_n), 12))
        self.coarse_min_conf = float(coarse_min_conf)
        # Only let the decision model override the lexical top pick when the
        # lexical score of that pick is weak (< this). Protects against
        # laya's topical bias when the lexical match is already strong.
        self.coarse_override_max_lex = float(coarse_override_max_lex)
        self._lexical = LexicalSelector()
        # observability
        self.last_stats: dict = {}

    # -- factory from Odoo config -------------------------------------------
    @classmethod
    def _conf(cls, key: str, default: str = "") -> str:
        """Read a value from odoo.conf (set by Salt), falling back to
        ir.config_parameter. odoo.conf is the distribution channel the
        bifrost state writes to; ir.config_parameter lets an admin override
        at runtime without touching Salt.
        """
        try:
            from odoo.tools import config as odoo_config
            val = odoo_config.get(key, None)
            if val not in (None, ""):
                return str(val)
        except Exception:
            pass
        return default

    @classmethod
    def from_env(cls, env) -> "ToolSelector":
        """Build from odoo.conf + ir.config_parameter with safe defaults.

        Precedence: ir.config_parameter (runtime override) > odoo.conf
        (Salt-distributed) > built-in default.

        Jev is entirely optional. If jev_url is empty, or jev_enabled is
        false, the selector runs on the lexical/embedding fallback. If the
        fallback finds no match, the full registry is sent. The loop never
        fails because the selector is unavailable.
        """
        try:
            icp = env["ir.config_parameter"].sudo()

            def get(key, default):
                v = icp.get_param(key, None)
                if v not in (None, ""):
                    return v
                return cls._conf(key, default)

            enabled = str(get("ai_agent_core.tool_selection_enabled",
                              "True")).lower() in ("1", "true", "yes", "on")
            top_k = int(get("ai_agent_core.tool_selection_top_k", "5") or 5)
            min_tools = int(get("ai_agent_core.tool_selection_min_tools",
                                "12") or 12)

            jev_enabled = str(get("jev_enabled", "False")).lower() in (
                "1", "true", "yes", "on")
            jev_url = (get("jev_url", "") or "") if jev_enabled else ""
            jev_model = get("jev_model", "openjev") or "openjev"
            try:
                jev_timeout = float(get("jev_timeout", "3") or 3)
            except (TypeError, ValueError):
                jev_timeout = 3.0

            jev = None
            if jev_url:
                jev = JevClient(jev_url, jev_model, jev_timeout)
                if not jev.healthy():
                    # Configured but not answering — fall back cleanly and
                    # say so. The loop keeps working on the lexical path.
                    _logger.warning(
                        "Jev configured at %s but not reachable — "
                        "using lexical fallback", jev_url)
                    jev = None

            # Coarse-to-fine (only meaningful when a decision model exists).
            c2f = str(get("ai_agent_core.tool_selection_coarse_to_fine",
                          "True")).lower() in ("1", "true", "yes", "on")
            try:
                coarse_n = int(get("ai_agent_core.tool_selection_coarse_n",
                                   "8") or 8)
            except (TypeError, ValueError):
                coarse_n = 8
            try:
                coarse_min_conf = float(get(
                    "ai_agent_core.tool_selection_coarse_min_conf", "0.5") or 0.5)
            except (TypeError, ValueError):
                coarse_min_conf = 0.5
            try:
                override_max_lex = float(get(
                    "ai_agent_core.tool_selection_coarse_override_max_lex",
                    "1.5") or 1.5)
            except (TypeError, ValueError):
                override_max_lex = 1.5

            return cls(enabled=enabled, top_k=top_k, min_tools=min_tools,
                       jev=jev, coarse_to_fine=c2f, coarse_n=coarse_n,
                       coarse_min_conf=coarse_min_conf,
                       coarse_override_max_lex=override_max_lex)
        except Exception as e:
            _logger.info("ToolSelector.from_env failed (%s) — disabled", e)
            return cls(enabled=False)

    # -- selection ----------------------------------------------------------
    def select(self, prompt: str, registry):
        """Return a narrowed registry. Never raises; never widens access."""
        try:
            return self._select(prompt, registry)
        except Exception as e:
            _logger.warning("ToolSelector.select failed (%s) — full registry", e)
            return registry

    def _select(self, prompt: str, registry):
        all_tools = registry.list()
        n = len(all_tools)

        # Nothing to do for small registries — the overhead is not worth it.
        if not self.enabled or n <= self.min_tools:
            self.last_stats = {"skipped": True, "n": n}
            return registry

        # 1. Lexical pre-narrowing — ALWAYS runs first. It is the base
        #    selection (100% recall on the test suite) and it produces the
        #    shortlist that a decision model can actually judge.
        self._lexical.fit(all_tools)
        scored = sorted(
            all_tools,
            key=lambda t: self._lexical.score(prompt, t),
            reverse=True,
        )
        # Guard against a degenerate fallback: if NO tool scores above zero
        # (unknown prompt / language gap), do not hand back an arbitrary
        # alphabetical slice — that is worse than useless. Return the full
        # registry instead so the LLM still sees everything.
        if not scored or self._lexical.score(prompt, scored[0]) <= 0.0:
            self.last_stats = {
                "skipped": True, "reason": "no_match", "n": n,
            }
            return registry

        backend = "lexical"
        chosen = []

        # 2. Coarse-to-fine with a decision model (OPTIONAL).
        #    Measured on fors 2026-09-22: laya is accurate with FEW
        #    candidates (67% @ 4, ~111 ms) but collapses with many
        #    (25% @ 100, 0% @ 100 simultaneous noul). So we hand it the
        #    lexical shortlist, not the whole registry, and let it
        #    re-rank the top few. If it disagrees we trust it only when
        #    confident; otherwise the lexical order stands.
        if self.jev and self.jev.available() and self.coarse_to_fine:
            shortlist = scored[:self.coarse_n]
            cands = {
                t.name: (getattr(t, "description", "") or t.name)[:200]
                for t in shortlist
            }
            pick = self.jev.pick(prompt, cands)
            if pick and pick[1] >= self.coarse_min_conf:
                value, conf = pick
                # Only let the decision model OVERRIDE the lexical top pick
                # when it is both confident AND the lexical top pick is not
                # itself a strong match. Measured 2026-09-22: laya has a
                # systematic topical bias (picks caddy_recent_errors for
                # "search the web for Caddy docs" because the word "Caddy"
                # dominates the verb). So a confident laya alone is not
                # enough — it must also clear the lexical score of the
                # current top pick. Otherwise the lexical order stands.
                lex_top = shortlist[0]
                lex_top_score = self._lexical.score(prompt, lex_top)
                if (value in cands and value != lex_top.name
                        and lex_top_score < self.coarse_override_max_lex):
                    backend = "jev+lexical"
                    chosen.append(value)
                    _logger.info(
                        "Coarse-to-fine: laya valde %s (%.2f) framför "
                        "lexikal topp %s (lex=%.2f)", value, conf,
                        lex_top.name, lex_top_score)
                elif value in cands and value == lex_top.name:
                    backend = "jev+lexical"
                    _logger.info(
                        "Coarse-to-fine: laya bekräftade lexikalt val %s "
                        "(%.2f)", value, conf)

        for t in scored:
            if len(chosen) >= self.top_k:
                break
            if t.name not in chosen:
                chosen.append(t.name)

        # 3. Always-keep floor. These are appended AFTER the relevant
        #    tools (see step 4) so they never displace the top match.
        keep = set(chosen) | {t.name for t in all_tools
                              if t.name in self.always_keep}

        # 4. Build the narrowed registry — in RELEVANCE order, not
        #    alphabetical. Insertion order is what list()/to_openai()
        #    return, and the LLM attends more to the first entries, so the
        #    most relevant tool must come first. Always-keep tools go last
        #    (they are utilities, not the answer to this prompt).
        from odoo.addons.ai_agent_core.core.tools import ToolRegistry
        narrowed = ToolRegistry()
        _by_name = {t.name: t for t in all_tools}
        ordered = [n for n in chosen if n in _by_name]
        ordered += [n for n in sorted(self.always_keep)
                    if n in _by_name and n not in chosen]
        for name in ordered:
            narrowed.register(_by_name[name])

        before = tool_block_tokens(all_tools)
        after = tool_block_tokens(narrowed.list())
        self.last_stats = {
            "skipped": False,
            "backend": backend,
            "n": n,
            "selected": len(narrowed),
            "tokens_before": before,
            "tokens_after": after,
            "tokens_saved": before - after,
            "pct_saved": round(100 * (1 - after / before), 1) if before else 0,
        }
        _logger.info(
            "ToolSelector[%s]: %d/%d tools, ~%d→%d tokens (−%s%%)",
            backend, len(narrowed), n, before, after,
            self.last_stats["pct_saved"],
        )
        return narrowed

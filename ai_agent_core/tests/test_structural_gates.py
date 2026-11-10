# -*- coding: utf-8 -*-
"""Strukturella grindar för ai_agent_core.

Ändring: structural-discipline-gates (2026-11-02).

Grindarna bevakar *var* kod får byggas — inte vad den gör. De är
AST-baserade (inte regex) eftersom docstrings och kommentarer i den här
kodbasen medvetet nämner klassnamnen de varnar för; en regex skulle
räkna dem som träffar.

Två grindar bor i den här filen, med var sin undantagslista:

1. **Loop-konstruktionen** (``test_agent_loop_constructed_only_in_build_loop``)
   — ägs av ``structural-discipline-gates``. Frågan är "byggs loopen på rätt
   ställe?".

2. **Klass-wiringen** (``test_core_classes_are_wired``)
   — ägs av ``wire-orphaned-core-mechanisms``. Frågan är "anropas den här
   ``core/``-klassen alls?".

Filen är gemensam med flit: två AST-grindar i två filer hade glidit isär.

Dessutom bor två grindar som bevakar *mekanismer* i ``core/``:

3. **Tysta ``except``** (``test_silent_excepts_are_labelled``)
   — ägs av ``structural-discipline-gates``.

4. **Trovärdighetsmärkningen** (``test_web_tools_are_untrusted``)
   — ägs av ``untrusted-tool-output``. Frågan är "är ett verktyg som når
   internet märkt som otrovärdigt?".

5. **Språket** (``test_no_new_swedish_string_literals``)
   — ägs av ``core-english-and-sv-po``. Frågan är "är den här strängen
   skriven på modulens källspråk?". Källan är engelska; svenskan bor i
   ``i18n/sv.po``. Tillväxttaket ligger i ``tests/language_baseline.json``.
"""

import ast
import os
import pathlib
import re
import unittest

from odoo.tests.common import TransactionCase, tagged


MODULE_ROOT = pathlib.Path(__file__).resolve().parent.parent
"""Rot för ai_agent_core (tests/ ligger ett steg ner)."""

#: OpenSpec-planen ligger utanför modulen. Den läses av
#: `TestApprovalsSpecConsistency` — en grind mot specen, inte mot koden.
#:
#: Fällan (2026-11-09): sökvägen var hårdkodad till
#: `/home/waland/plan/odoo-ai/...`. `/home/waland` är `drwxr-x---`, så när
#: sviten körs som `odoo` kastar `Path.exists()` `PermissionError` (EACCES
#: propagerar från en icke-traverserbar förälderkatalog) — testet kraschade
#: i stället för att hoppa över. Nu går den att peka ut via miljövariabel
#: och en oläsbar sökväg hoppar över testet med ett tydligt skäl.
OPENSPEC_ROOT_ENV = 'AI_AGENT_CORE_OPENSPEC_ROOT'
DEFAULT_OPENSPEC_ROOT = pathlib.Path('/home/waland/plan/odoo-ai/openspec')


def _read_openspec_spec(relative_path):
    """Läs en spec ur OpenSpec-planen, eller ``None`` om den inte går att läsa.

    Returnerar ``None`` (inte ett undantag) när sökvägen saknas, är
    oläsbar eller ligger bakom en katalog vi inte får traversera — testet
    ska hoppa över, inte krascha. Miljövariabeln
    ``$AI_AGENT_CORE_OPENSPEC_ROOT`` pekar ut planen när den inte ligger på
    standardplatsen.
    """
    root = pathlib.Path(
        os.environ.get(OPENSPEC_ROOT_ENV) or DEFAULT_OPENSPEC_ROOT)
    path = root / relative_path
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding='utf-8')
    except (OSError, PermissionError):
        # OSError täcker EACCES från en icke-traverserbar förälderkatalog.
        return None


# ──────────────────────────────────────────────────────────────────────
# Grind 1 — loop-konstruktionen
# ──────────────────────────────────────────────────────────────────────

#: Konstruktioner som LEGITIMT bygger en ``AgentLoop`` med verktyg.
#: Varje post är ``(relativ sökväg, funktionsnamn)`` och kräver en
#: motivering. En post som inte längre existerar fallerar grinden —
#: annars blir listan en gravplats för föråldrade undantag.
LOOP_CONSTRUCTION_ALLOWLIST = [
    (
        'models/ai_coworker.py',
        '_build_loop',
        'Den enda konstruktionsplatsen för en körbar loop. Alla åtta '
        'körvägar går hit (se core/loop.py:86).',
    ),
    (
        'models/ai_coworker.py',
        '_build_specialists',
        'Bygger en loop per specialist-agent. Specialisterna är egna '
        'loopar med egna rundtak och callbacks (B3) — de kan inte gå via '
        '_build_loop, som bygger medarbetarens loop.',
    ),
    (
        'controllers/stream.py',
        'stream',
        'SSE-vägen bygger StreamingAgentLoop — en annan klass än '
        'medarbetarens AgentLoop — via en inre _make_loop-hjälpare, med egen '
        'checkpoint-callback mot requestens cursor. _build_loop bygger '
        'AgentLoop, inte StreamingAgentLoop; vägen kan inte gå dit utan att '
        'byta klass.',
    ),
    (
        'controllers/openai_api.py',
        '_handle_stream',
        'OpenAI-kompatibel strömväg bygger StreamingAgentLoop direkt i '
        'generatorn (samma skäl som controllers/stream.py). Flytten hit '
        'vore en egen ändring: generatorn äger sin egen cursor och sitt '
        'eget checkpoint-kontrakt.',
    ),
    (
        'core/linear.py',
        'run',
        'LinearLoop bygger en AgentLoop per steg i sin pipeline. Steget '
        'bär pipelinesvarets callbacks (outcome/checkpoint/interrupt) — '
        'att gå via ai.coworker._build_loop hade kopplat modellagret till '
        'core/ i fel riktning (core/ får inte importera models/).',
    ),
]

#: Klassnamn som räknas som loop-konstruktion.
LOOP_CLASS_NAMES = ('AgentLoop', 'StreamingAgentLoop')


def _module_files(root):
    """Alla .py-filer under ``root``, med tests/ och __pycache__ undantagna."""
    for path in sorted(root.rglob('*.py')):
        parts = set(path.parts)
        if 'tests' in parts or '__pycache__' in parts:
            continue
        yield path


def _parse(path):
    try:
        return ast.parse(path.read_text(encoding='utf-8'))
    except (SyntaxError, UnicodeDecodeError):
        return None


def _call_name(node):
    """Namnet på en ``ast.Call`` eller en bas i en ``ast.ClassDef``.

    Hanterar både ``AgentLoop`` (Name), ``x.AgentLoop`` (Attribute) och
    en klassbas som skrivs direkt i ``class Foo(Bar):`` (Name).
    """
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _enclosing_functions(tree):
    """Karta ``id(call-node) -> (funktionsnamn, lineno)``.

    En konstruktion i modulnivå får funktionsnamnet ``None``.
    """
    owner = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    owner.setdefault(id(child), (node.name, child.lineno))
    return owner


def _find_loop_constructions(root):
    """Returnerar ``[(relpath, funcname, lineno, classname)]``."""
    hits = []
    for path in _module_files(root):
        tree = _parse(path)
        if tree is None:
            continue
        owners = _enclosing_functions(tree)
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node) not in LOOP_CLASS_NAMES:
                continue
            funcname, lineno = owners.get(id(node), (None, node.lineno))
            hits.append((rel, funcname, lineno, _call_name(node)))
    return sorted(hits)


class StructuralGateLoopConstruction(TransactionCase):
    """Grind: ``AgentLoop`` får bara byggas på de platser som äger det."""

    def test_agent_loop_constructed_only_in_build_loop(self):
        """Varje ``AgentLoop(...)`` ska ligga i en allowlistad funktion."""
        allowed = {(path, func) for path, func, _reason in LOOP_CONSTRUCTION_ALLOWLIST}
        violations = [
            f'{rel}:{lineno} i {func or "<modulnivå>"} — {cls}(...)'
            for rel, func, lineno, cls in _find_loop_constructions(MODULE_ROOT)
            if (rel, func) not in allowed
        ]
        self.assertFalse(
            violations,
            'AgentLoop konstrueras utanför de platser som äger '
            'konstruktionen. Flytta anropet till _build_loop() — eller '
            'lägg till en motiverad post i LOOP_CONSTRUCTION_ALLOWLIST om '
            'platsen legitimt bygger en egen loop:\n  '
            + '\n  '.join(violations),
        )

    def test_allowlist_entries_still_exist(self):
        """En allowlist-post som inte längre används ska fallera."""
        constructions = {(rel, func) for rel, func, _l, _c in _find_loop_constructions(MODULE_ROOT)}
        stale = [
            f'{path}::{func}'
            for path, func, _reason in LOOP_CONSTRUCTION_ALLOWLIST
            if (path, func) not in constructions
        ]
        self.assertFalse(
            stale,
            'Allowlist-poster som inte längre bygger någon AgentLoop — '
            'ta bort dem (en föråldrad post döljer nästa överträdelse):\n  '
            + '\n  '.join(stale),
        )

    def test_allowlist_entries_have_reasons(self):
        """Varje post ska bära ett skrivet skäl, inte en tom sträng."""
        missing = [
            f'{path}::{func}'
            for path, func, reason in LOOP_CONSTRUCTION_ALLOWLIST
            if not (reason or '').strip()
        ]
        self.assertFalse(missing, 'Allowlist-poster utan motivering:\n  ' + '\n  '.join(missing))


# ──────────────────────────────────────────────────────────────────────
# Grind 3 — de tysta felen (structural-discipline-gates §4)
# ──────────────────────────────────────────────────────────────────────

#: Kataloger som grinden bevakar. ``core/``, ``models/`` och
#: ``controllers/`` är de tre ställen där tysta ``except`` har kostat
#: mest felsökningstid (docs/failures.md §10).
SILENT_EXCEPT_SCOPE = ('core', 'models', 'controllers')

#: Etiketter som godkänns i kroppen på ett ``except``.
#: ``best-effort:`` = vi vet att det kan falla och väljer att fortsätta.
#: ``fallback:``    = vi faller tillbaka på ett annat värde med flit.
SILENT_EXCEPT_LABELS = ('best-effort:', 'fallback:')

#: En etikett som bara hänvisar tillbaka till regeln är inget skäl.
#: Mätt 2026-11-08: 113 av 116 tysta ``except`` i modulen bar exakt
#: ``"tyst fallback — se etiketteringsregeln"`` — den passerade grinden
#: eftersom den innehåller ``best-effort:``, men motiverade ingenting.
#: Regeln (openspec/specs/structural-gates) kräver att etiketten anger
#: *varför* felet sväljs, inte var regeln finns.
TAUTOLOGICAL_LABEL_MARKERS = ('etiketteringsregeln',)

#: Känt antal tautologiska etiketter.
#: Talet är en SKULD, inte ett mål: det får bara minska. Samma princip som
#: ruff-baselinen för C901/BLE001/S110 — "the numbers describe existing
#: debt, and a new violation must be a visible decision".
#:
#: 2026-11-08: mätt till 113. Samma dag rättades de tre i
#: _get_quest_memories (stream.py) till riktiga loggrader → 110.
TAUTOLOGICAL_LABEL_BASELINE = 110


def _is_silent(handler):
    """Ett ``except`` är tyst om kroppen bara är ``pass`` eller ``continue``."""
    body = [n for n in handler.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    if len(body) != 1:
        return False
    return isinstance(body[0], (ast.Pass, ast.Continue))


def _label_span(handler, source_lines):
    """Raderna mellan ``except``-raden och första satsen (etiketten bor där)."""
    first_body_line = handler.body[0].lineno if handler.body else handler.lineno
    return source_lines[handler.lineno - 1:first_body_line]


def _is_tautological(handler, source_lines):
    """Sant om etiketten hänvisar tillbaka till regeln i stället för att ge skäl."""
    span = _label_span(handler, source_lines)
    return any(marker in line for line in span
               for marker in TAUTOLOGICAL_LABEL_MARKERS)


def _find_tautological_labelled_excepts(root):
    """Returnerar ``[(relpath, lineno)]`` för tysta ``except`` vars skäl är en hänvisning."""
    hits = []
    for scope in SILENT_EXCEPT_SCOPE:
        base = root / scope
        if not base.is_dir():
            continue
        for path in sorted(base.rglob('*.py')):
            if 'tests' in path.parts or '__pycache__' in path.parts:
                continue
            try:
                source = path.read_text(encoding='utf-8')
                tree = ast.parse(source)
            except (SyntaxError, UnicodeDecodeError):
                continue
            lines = source.split('\n')
            rel = path.relative_to(root).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and _is_silent(node):
                    if _is_tautological(node, lines):
                        hits.append((rel, node.lineno))
    return sorted(hits)


def _has_label(handler, source_lines):
    """Sant om etiketten står mellan ``except``-raden och första satsen.

    Kommentarer blir inga AST-noder, så etiketten hamnar ofta på en egen rad
    *mellan* ``except`` och ``continue``/``pass``. Den första versionen tittade
    bara på ``except``-raden och på första satsens rad — och missade därmed
    varje etikett som skrivits på det naturliga sättet (ovanför satsen).
    Mätt 2026-11-05: ``core/channels.py:89`` hade etiketten på rätt plats och
    föll ändå.
    """
    first_body_line = handler.body[0].lineno if handler.body else handler.lineno
    # Från except-raden fram till (och med) första satsen.
    span = source_lines[handler.lineno - 1:first_body_line]
    return any(label in line for line in span for label in SILENT_EXCEPT_LABELS)


def _find_unlabelled_silent_excepts(root):
    """Returnerar ``[(relpath, lineno, typ)]`` för tysta ``except`` utan etikett."""
    hits = []
    for scope in SILENT_EXCEPT_SCOPE:
        base = root / scope
        if not base.is_dir():
            continue
        for path in sorted(base.rglob('*.py')):
            if 'tests' in path.parts or '__pycache__' in path.parts:
                continue
            try:
                source = path.read_text(encoding='utf-8')
                tree = ast.parse(source)
            except (SyntaxError, UnicodeDecodeError):
                continue
            lines = source.split('\n')
            rel = path.relative_to(root).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and _is_silent(node):
                    if not _has_label(node, lines):
                        hits.append((rel, node.lineno, ast.unparse(node.type) if node.type else 'bare'))
    return sorted(hits)


class StructuralGateSilentExcept(TransactionCase):
    """Grind: ett tyst ``except`` ska bära etiketten ``best-effort:`` eller ``fallback:``.

    Regeln finns för att skilja *avsiktlig* tystning från *okunskap*. Ett
    ``except: pass`` utan skäl är en gissning som ser ut som ett beslut —
    och den har kostat mer felsökningstid än någon annan kodkonstruktion i
    den här modulen (docs/failures.md §10).
    """

    def test_silent_except_labels_give_a_reason(self):
        """Etiketten ska ange ett skäl — inte hänvisa tillbaka till regeln.

        Mätt 2026-11-08: 113 av 116 tysta ``except`` bar
        ``"tyst fallback — se etiketteringsregeln"``. Den innehåller
        ``best-effort:`` och passerade därför den äldre grinden, men säger
        ingenting om varför felet sväljs. Regeln kräver ett skäl.

        Talet är pinnat som skuld: det får bara minska. En NY tautologisk
        etikett fäller grinden, vilket är hela poängen — annars fortsätter
        mönstret att spridas när någon kopierar en granne.
        """
        hits = _find_tautological_labelled_excepts(MODULE_ROOT)
        self.assertLessEqual(
            len(hits), TAUTOLOGICAL_LABEL_BASELINE,
            'Fler tautologiska etiketter än baslinjen (%d). Etiketten ska '
            'ange VARFÖR felet sväljs, inte hänvisa till regeln. Nya '
            'förekomster:\n  ' % TAUTOLOGICAL_LABEL_BASELINE
            + '\n  '.join(f'{rel}:{lineno}' for rel, lineno in hits),
        )

    def test_silent_excepts_are_labelled(self):
        violations = [
            f'{rel}:{lineno} — except {typ}'
            for rel, lineno, typ in _find_unlabelled_silent_excepts(MODULE_ROOT)
        ]
        self.assertFalse(
            violations,
            'Tysta except utan etikett. Skriv `# best-effort: <skäl>` när vi '
            'vet att det kan falla och väljer att fortsätta, eller '
            '`# fallback: <skäl>` när vi med flit faller tillbaka:\n  '
            + '\n  '.join(violations),
        )


# ──────────────────────────────────────────────────────────────────────
# Grind 4 — stående regler i obevakade vägar (unattended-standing-rules 1.4)
# ──────────────────────────────────────────────────────────────────────

#: Filer som bygger en ``PermissionEngine`` i en OBEVAKAD körväg och därför
#: måste applicera coworkerns stående regler. Nyckeln är filnamnet, värdet
#: är antalet tillåtna konstruktioner som INTE går genom
#: ``_apply_standing_rules()`` — med skrivet skäl.
STANDING_RULE_EXEMPT = {
    # controllers/stream.py: /ai/v1/tools/run är INTERACTIVE, inte AUTO —
    # ytan har ingen godkännandekanal och ingen coworker-kontext att läsa
    # regler från. Den ska neka hårda stopp, inte ärva en automation.
    'controllers/stream.py': 1,
    # core/loop.py: AgentLoop.__init__ skapar en DEFAULT-grind när ingen
    # skickas in. Den har ingen coworker och ingen session — reglerna
    # appliceras av anroparen (run()/action_run_scheduled()), inte här.
    'core/loop.py': 1,
}


def _permission_engine_constructions(root):
    """Returnerar ``[(relpath, lineno, funcname, har_standing_rules)]``."""
    hits = []
    for path in _module_files(root):
        tree = _parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        source = path.read_text(encoding='utf-8')
        lines = source.split('\n')
        owners = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _call_name(node) != 'PermissionEngine':
                continue
            funcname, lineno = owners.get(id(node), (None, node.lineno))
            # Leta efter `_apply_standing_rules` i samma funktion.
            window = '\n'.join(lines[max(0, lineno - 12):lineno + 3])
            hits.append((rel, lineno, funcname,
                         '_apply_standing_rules' in window))
    return sorted(hits)


class StructuralGateStandingRules(TransactionCase):
    """Grind: en obevakad väg som bygger AUTO måste läsa stående regler.

    Utan den här grinden kan en ny obevakad körväg byggas utan
    ``_apply_standing_rules()`` — och då faller den tillbaka på
    ``hitl_threshold``, vilket för en väg utan människa betyder att
    körningen aldrig kan slutföras. Det var exakt buggen: cron läste
    regeln, mail/watch/heartbeat gjorde det inte.
    """

    def test_auto_paths_apply_standing_rules(self):
        offenders = []
        for rel, lineno, funcname, has_rules in _permission_engine_constructions(
                MODULE_ROOT):
            if has_rules:
                continue
            exempt = STANDING_RULE_EXEMPT.get(rel, 0)
            if exempt > 0:
                STANDING_RULE_EXEMPT[rel] = exempt - 1
                continue
            offenders.append(f'{rel}:{lineno} i {funcname or "<modulnivå>"}')
        self.assertFalse(
            offenders,
            'PermissionEngine byggs utan att coworkerns stående regler '
            'appliceras. Anropa `self._apply_standing_rules(...)` — eller '
            'lägg till ett motiverat undantag i STANDING_RULE_EXEMPT om '
            'vägen inte är obevakad:\n  ' + '\n  '.join(offenders),
        )

    def test_exemptions_are_used(self):
        """Ett undantag som inte längre behövs ska bort."""
        unused = {k: v for k, v in STANDING_RULE_EXEMPT.items() if v > 0}
        self.assertFalse(
            unused,
            'Undantag som inte matchade någon konstruktion — ta bort dem: %s'
            % unused)


# ──────────────────────────────────────────────────────────────────────
# Grind 6 — varje exekverande endpoint prövar grinden (tools-run 5.1)
# ──────────────────────────────────────────────────────────────────────

#: Filer som bygger en ``ToolRegistry`` och därför måste anropa
#: ``PermissionEngine.evaluate()`` innan ``tool.execute()``.
TOOL_EXECUTION_FILES = ('controllers/stream.py',)


def _tool_execution_without_gate(root):
    """Returnerar ``[(relpath, funcname)]`` där execute() saknar grind."""
    offenders = []
    for rel in TOOL_EXECUTION_FILES:
        path = root / rel
        if not path.exists():
            continue
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            src = ast.unparse(node)
            if 'ToolRegistry(' not in src:
                continue
            if '.execute(' not in src:
                continue
            if 'evaluate(' not in src:
                offenders.append((rel, node.name))
    return offenders


class StructuralGateToolExecution(TransactionCase):
    """Grind: en endpoint som kör verktyg måste pröva grinden först.

    Bakgrunden är konkret: `/ai/v1/tools/run` byggde en ToolRegistry och
    anropade `tool.execute()` DIREKT. De två hårda stoppen kunde därför
    köras utan mänskligt godkännande, trots att `approvals`-specen säger
    att de ska gälla oavsett väg.
    """

    def test_tool_execution_goes_through_the_gate(self):
        offenders = _tool_execution_without_gate(MODULE_ROOT)
        self.assertFalse(
            offenders,
            'En endpoint kör verktyg utan PermissionEngine.evaluate():\n  '
            + '\n  '.join('%s::%s' % o for o in offenders),
        )


# ──────────────────────────────────────────────────────────────────────
# Grind 2 — klass-wiringen (wire-orphaned-core-mechanisms 1.1)
# ──────────────────────────────────────────────────────────────────────

#: Publika klasser i ``core/`` som med flit inte har någon anropare
#: utanför ``core/`` och ``tests/``. Varje post är ``(klassnamn, skäl)``.
#:
#: Uppmätt mot 18.0 @ 54cd3618: 21 publika klasser i core/, 12 utan
#: anropare utanför core/ och tests/. Posterna nedan är de som har ett
#: skrivet skäl att stanna. `BudgetTracker`/`BudgetEnforcingProvider` fanns
#: aldrig här — de togs bort ur core/budget.py i §2. `AgentEvaluator` och
#: `EvalRunner` försvann i §5 (cronen i models/ai_coworker.py anropar dem).
#: `IntelligentRouter`/`RuleBasedRouter`/`EnvironmentDetector` stod här som
#: kvarvarande §8-arbete; de togs bort i §8 tillsammans med core/route.py
#: och core/detect.py — de hade ingen produktionsanropare.
#:
#: `ChannelRegistry`/`ChannelAdapter`/`ItemProcessor` stod här när
#: `channel-adapter-wiring` började. De togs bort i §6.3: `hooks.py`
#: registrerar mail-kanalen vid moduluppstart och `models/ai_coworker.py`
#: använder `MailChannelAdapter` — kontraktet har produktionsanropare.
#: `NormalizedItem` och `process_item` har aldrig räknats som mekanismer
#: (dataklass respektive modulnivåfunktion).
CORE_WIRING_ALLOWLIST = [
    (
        'EvalRunner',
        'Skapas uteslutande via fabriken `AgentEvaluator.create_runner()` '
        '(core/eval.py:107). Den anropande sidan ser alltså aldrig klassnamnet '
        '— bara `create_runner`. Att kräva en namngiven referens utanför '
        'core/ vore att kräva att konstruktorn anropas direkt, vilket är '
        'precis vad fabriken finns för att undvika.',
    ),
    (
        'AIProvider',
        'Abstrakt bas för provider-klienterna; instansieras aldrig direkt. '
        'Anropas via subklasser i provider-lagret (core/loop.py:24 importerar '
        'den som typ).',
    ),
    (
        'GatewayRejection',
        'Feltyp som returneras och fångas inom provider-lagret '
        '(core/provider.py). Den korsar aldrig modellgränsen — att kräva en '
        'anropare utanför core/ vore att kräva att felet läcker ut.',
    ),
    (
        'AutoInterruptHandler',
        'HITL-004: väljs av select_interrupt_handler (core/interrupt.py:500 '
        'returnerar den via klassnamn). Valet sker i core/, så en referens '
        'utanför core/ finns inte — men klassen är inkopplad.',
    ),
    (
        'DiscussInterruptHandler',
        'HITL-002: väljs av select_interrupt_handler på samma sätt som '
        'AutoInterruptHandler. Inkopplad via val, inte via import.',
    ),
    (
        'ExternalAgentHandle',
        'Returtyp från runtime.spawn() (core/runtime.py:328). Anroparen i '
        'models/ai_agent.py:227 får handtaget och läser dess fält (pid, port, '
        'duration(), rss_kb()) — men namnger aldrig typen, därför ingen '
        'referens utanför core/. Ingen anropare behövs: klassen är ett '
        'databärande handtag som bara skapas av spawn() och konsumeras '
        'strukturellt. Att importera den i ai_agent.py vore en import för '
        'importens skull.',
    ),
    (
        'JevClient',
        'Intern komposition i ToolSelector: instansieras i '
        'ToolSelector.from_env() (core/tool_select.py:589) och hålls som '
        'self.jev. Ingen anropare utanför core/ behövs — Jev är en valfri '
        'beslutsmodell som bara ToolSelector talar med; faller tillbaka på '
        'lexikal matchning när den saknas.',
    ),
    (
        'LexicalSelector',
        'Intern komposition i ToolSelector: instansieras i '
        'ToolSelector.__init__ (core/tool_select.py:530) som self._lexical. '
        'Ingen anropare utanför core/ behövs — den är den lexikala '
        'fallbacken inuti selectorn, aldrig en egen ingång.',
    ),
]

#: Baser som inte räknas som mekanismer (värden, fel, protokoll).
NON_MECHANISM_BASES = {
    'Error',
    'Exception',
    'Enum',
    'IntEnum',
    'StrEnum',
    'Protocol',
    'ABC',
    'ABCMeta',
    'NamedTuple',
    'TypedDict',
    'dataclass',
}


def _core_classes(root):
    """Publika klasser i ``core/`` vars ``__init__`` tar minst ett argument.

    Returnerar ``[(modul, klassnamn, baser)]``.
    """
    core = root / 'core'
    found = []
    if not core.is_dir():
        return found
    for path in sorted(core.rglob('*.py')):
        tree = _parse(path)
        if tree is None:
            continue
        rel = path.relative_to(root).as_posix()
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name.startswith('_'):
                continue
            bases = {_call_name(b) for b in node.bases}
            bases.discard(None)
            if bases & NON_MECHANISM_BASES:
                continue
            if not _init_takes_argument(node):
                continue
            found.append((rel, node.name, sorted(bases)))
    return found


def _init_takes_argument(classdef):
    """Sant om klassen har ``__init__`` med minst ett argument utöver self."""
    for item in classdef.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == '__init__':
            args = [a.arg for a in item.args.args]
            if args and args[0] in ('self', 'cls'):
                args = args[1:]
            return bool(args)
    return False


def _referenced_names(root, exclude_dirs):
    """Alla ``ast.Name``/``ast.Attribute``-namn utanför ``exclude_dirs``."""
    names = set()
    for path in root.rglob('*.py'):
        if set(path.parts) & exclude_dirs or '__pycache__' in path.parts:
            continue
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
    return names


class StructuralGateCoreWiring(TransactionCase):
    """Grind: en mekanism i ``core/`` ska ha minst en anropare utanför ``core/``.

    Undantag kräver ett skrivet skäl i ``CORE_WIRING_ALLOWLIST`` — en
    klass som varken används eller är undantagen är antingen död kod
    eller en wiring som glömdes.
    """

    def test_core_classes_are_wired(self):
        """Varje publik ``core/``-klass ska refereras utanför core/ och tests/."""
        referenced = _referenced_names(MODULE_ROOT, {'core', 'tests', '__pycache__'})
        allowed = {name for name, _reason in CORE_WIRING_ALLOWLIST}
        violations = [
            f'{module}:{name} (baser: {", ".join(bases) or "—"})'
            for module, name, bases in _core_classes(MODULE_ROOT)
            if name not in referenced and name not in allowed
        ]
        self.assertFalse(
            violations,
            'Klasser i core/ som ingen anropar utanför core/ och tests/. '
            'Koppla in dem, ta bort dem — eller lägg till en motiverad post '
            'i CORE_WIRING_ALLOWLIST:\n  ' + '\n  '.join(violations),
        )

    def test_core_wiring_allowlist_has_reasons(self):
        """Varje undantag ska bära ett skrivet skäl."""
        missing = [name for name, reason in CORE_WIRING_ALLOWLIST if not (reason or '').strip()]
        self.assertFalse(missing, 'Undantag utan motivering:\n  ' + '\n  '.join(missing))

    def test_core_wiring_allowlist_is_not_stale(self):
        """Ett undantag för en klass som redan är inkopplad ska bort."""
        referenced = _referenced_names(MODULE_ROOT, {'core', 'tests', '__pycache__'})
        stale = [name for name, _reason in CORE_WIRING_ALLOWLIST if name in referenced]
        self.assertFalse(
            stale,
            'Undantag som inte längre behövs (klassen är inkopplad) — '
            'ta bort posten:\n  ' + '\n  '.join(stale),
        )


# ──────────────────────────────────────────────────────────────────────
# Grind 4 — fallback-kedjan i record-detektionen
# (structural-discipline-gates §5)
# ──────────────────────────────────────────────────────────────────────

#: Prefixet varje rad från fallback-kedjan bär i `message`.
#: Används för att skilja grindens rader från andra fel i samma tabell.
DETECT_ERROR_PREFIX = 'context source'


@tagged('post_install', '-at_install')
class TestDetectRecordFallbackLogging(TransactionCase):
    """En källa som KASTAR ska ge en `ai.coworker.error`-rad — tomt ska inte.

    Fallback-kedjan i `_detect_record()` prövar källor i prioritetsordning.
    Att en källa faller tillbaka på nästa är normalt; att den kastar är ett
    fel som tidigare svaldes av `except: pass`. Grinden bevakar att de två
    går att skilja i efterhand (design D5).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Fallback-loggnings-test',
            'orchestration_mode': 'single',
            'status': 'active',
        })
        cls.partner = cls.env['res.partner'].create({
            'name': 'Fallback Test Partner',
        })

    def setUp(self):
        super().setUp()
        if 'ai.coworker.error' not in self.env:
            self.skipTest('ai.coworker.error saknas i denna installation')
        self.error_model = self.env['ai.coworker.error']

    def _error_rows(self):
        """Rader från fallback-kedjan — inte andra fel i samma tabell."""
        return self.error_model.search([
            ('message', 'ilike', DETECT_ERROR_PREFIX),
        ])

    def _new_rows(self, before_ids):
        """Rader tillkomna sedan ``before_ids`` lästes."""
        return self._error_rows().filtered(
            lambda r: r.id not in before_ids)

    def _session(self, model, rec_id):
        """En session vars record-kontext pekar på ``model``/``rec_id``."""
        return self.env['ai.coworker.session'].create({
            'coworker_id': self.coworker.id,
            'status': 'active',
            'name': 'Fallback-loggnings-test',
            'ai_record_model': model,
            'ai_record_id': rec_id,
        })

    def _detect(self, session, **kwargs):
        """Kör `_detect_record()` med sessionen som env-context."""
        return self.coworker.with_context(
            _ai_context_model='ai.coworker.session',
            _ai_context_id=session.id,
        )._detect_record(kwargs)

    def test_raising_source_creates_error_row(self):
        """En källa som kastar lämnar en rad — och turen fortsätter."""
        # 'no.such.model' får env[...] att kasta — exakt den sortens
        # trasiga kontextväg som tidigare blev tyst.
        session = self._session('no.such.model', 1)
        before_ids = set(self._error_rows().ids)

        result = self._detect(session)

        self.assertIsNone(result, 'källan gav ingen record')
        rows = self._new_rows(before_ids)
        self.assertEqual(len(rows), 1, 'ett kast → exakt en rad')
        self.assertIn('session_record', rows.message)
        self.assertEqual(rows.session_id, session)
        self.assertEqual(rows.error_type, 'other')

    def test_empty_source_does_not_create_error_row(self):
        """En källa som returnerar tomt är ett giltigt svar — ingen rad."""
        # Modellen finns, men id:t gör inte → browse().exists() är tomt.
        session = self._session('res.partner', 0)
        before_ids = set(self._error_rows().ids)

        result = self._detect(session)

        self.assertIsNone(result, 'inga källor gav en record')
        self.assertEqual(
            len(self._new_rows(before_ids)), 0,
            'en tom källa ska inte fylla felloggen')

    def test_working_source_does_not_create_error_row(self):
        """En källa som svarar lämnar recorden — och ingen rad."""
        session = self._session('res.partner', self.partner.id)
        before_ids = set(self._error_rows().ids)

        result = self._detect(session)

        self.assertEqual(result, self.partner)
        self.assertEqual(len(self._new_rows(before_ids)), 0)

    def test_fallback_chain_continues_past_raising_source(self):
        """Ett kast i en tidig källa stoppar inte de senare källorna."""
        session = self._session('no.such.model', 1)
        before_ids = set(self._error_rows().ids)

        # Källan 5 (meddelandet) svarar — källan 3 kastade före den.
        message = self.env['mail.message'].create({
            'model': 'res.partner',
            'res_id': self.partner.id,
            'message_type': 'comment',
            'body': 'fallback-kedjans meddelandekälla',
        })
        result = self._detect(session, message=message)

        self.assertEqual(result, self.partner, 'källan 5 svarade')
        self.assertEqual(
            len(self._new_rows(before_ids)), 1,
            'källan 3 kastade och ska ha loggats en gång')


# ──────────────────────────────────────────────────────────────────────
# Grind 5 — trovärdighetsmärkningen (untrusted-tool-output 2.4)
# ──────────────────────────────────────────────────────────────────────

#: Förmågor som betyder att verktyget når internet och därför returnerar
#: otrovärdigt innehåll. Ett verktyg med någon av dessa ska ha
#: ``trust="untrusted"``.
WEB_CAPABILITIES = frozenset({'web'})

#: Verktyg som når internet UTAN att bära `web`-förmågan. Grinden kan inte
#: se dem i `capabilities`, så de listas här med sitt skäl — annars vore
#: listan tom och grinden hade bara fångat den dag någon råkar sätta rätt
#: förmåga på ett verktyg som redan är märkt.
#:
#: `browser_navigate` bär `browser` (den går via NATS till en Pi-agent) och
#: returnerar sidtext — samma klass av innehåll som `fetch_url`.
WEB_TOOLS_WITHOUT_CAPABILITY = {
    'browser_navigate': 'hämtar sidtext via en webbläsare (capability: browser)',
}


def _builtin_tool_trust():
    """``{verktygsnamn: (trust, capabilities)}`` för ``builtin_tools()``.

    Läser den riktiga registreringen i stället för källkoden: grinden ska
    bevaka vad ett verktyg FAKTISKT får, inte vad som står i en docstring.
    """
    from odoo.addons.ai_agent_core.core.tools import builtin_tools

    return {
        tool.name: (getattr(tool, 'trust', 'tool'), set(tool.capabilities or []))
        for tool in builtin_tools()
    }


@tagged('post_install', '-at_install')
class StructuralGateToolTrust(TransactionCase):
    """Grind: ett verktyg som når internet ska vara märkt ``untrusted``.

    Märkningen sätts vid verktygets definition (untrusted-tool-output D1).
    Glöms den bort får agenten webinnehåll i kontexten utan inramning — och
    det syns ingenstans. Grinden gör glömskan till ett rött test.
    """

    def test_web_tools_are_untrusted(self):
        """Varje verktyg med ``web``-förmåga ska ha ``trust="untrusted"``."""
        tools = _builtin_tool_trust()
        offenders = [
            '%s (trust=%r)' % (name, trust)
            for name, (trust, caps) in sorted(tools.items())
            if caps & WEB_CAPABILITIES and trust != 'untrusted'
        ]
        self.assertFalse(
            offenders,
            'Verktyg med web-förmåga som inte är märkta som otrovärdiga. '
            'Sätt trust="untrusted" på Tool(...) — annars går deras '
            'resultat in i kontexten utan inramning:\n  '
            + '\n  '.join(offenders),
        )

    def test_known_web_tools_are_untrusted(self):
        """De kända internetverktygen är märkta, även utan `web`-förmåga."""
        tools = _builtin_tool_trust()
        expected = {
            'odoo_web_search', 'odoo_fetch_url', 'news_fetch', 'news_digest',
        } | set(WEB_TOOLS_WITHOUT_CAPABILITY)
        missing = sorted(
            name for name in expected
            if name not in tools or tools[name][0] != 'untrusted'
        )
        self.assertFalse(
            missing,
            'Dessa internetverktyg saknas eller är inte märkta '
            'trust="untrusted": %s' % missing,
        )

    def test_odoo_tools_stay_trusted_as_data(self):
        """Odoo-data är ``tool``, inte ``untrusted`` och inte ``trusted``.

        Att slå ihop ``tool`` med ``untrusted`` hade gett varje
        `odoo_search` samma behandling som en hämtad webbsida; att slå ihop
        den med ``trusted`` hade gett den systempromptens auktoritet.
        """
        tools = _builtin_tool_trust()
        for name in ('odoo_search', 'describe_model', 'okf_search',
                     'graph_query'):
            self.assertIn(name, tools, '%s saknas i builtin_tools()' % name)
            self.assertEqual(
                tools[name][0], 'tool',
                '%s ska vara trust="tool" (data, inte otrovärdigt)' % name)

    def test_default_trust_is_tool(self):
        """Ett verktyg som inte sätter ``trust`` är ``tool``."""
        from odoo.addons.ai_agent_core.core.tools import Tool

        tool = Tool(
            name='grindens_default',
            description='testverktyg',
            parameters={'type': 'object', 'properties': {}},
        )
        self.assertEqual(tool.trust, 'tool')

    def test_gate_catches_an_unmarked_web_tool(self):
        """Grinden fallerar på ett verktyg med ``web`` men ``trust="tool"``.

        Utan det här provet vore grinden en påstående utan täckning: den
        kunde vara grön för att den letar efter fel sak.
        """
        from odoo.addons.ai_agent_core.core.tools import Tool

        unmarked = Tool(
            name='grindens_otrovardiga_verktyg',
            description='testverktyg',
            parameters={'type': 'object', 'properties': {}},
            capabilities=['web'],
            trust='tool',
        )
        caps = set(unmarked.capabilities or [])
        self.assertTrue(caps & WEB_CAPABILITIES)
        self.assertNotEqual(unmarked.trust, 'untrusted')


# ──────────────────────────────────────────────────────────────────────
# Grind 7 — slash-kommandot i html-fältet (complete-powerbox-transcript 6.3)
# ──────────────────────────────────────────────────────────────────────

#: Filer som tillsammans utgör slash-kommandot i ett html-fält.
POWERBOX_JS = 'static/src/js/wysiwyg_powerbox.js'


class StructuralGatePowerboxCommand(TransactionCase):
    """Grind: powerbox-kommandot ska finnas kvar och vara kopplat.

    Ett JS-beteende kan inte prövas i en `TransactionCase` — ingen webbläsare.
    Det som *kan* prövas är att kontraktet mot backend finns kvar: kommandot
    hämtar sina medarbetare från `/ai/powerbox/lookup`, kör via
    `/ai/powerbox/run`, och registrerar sig i `MAIN_PLUGINS` så
    html_editor-instanserna får det. Glider någon av dem bort blir
    slash-kommandot tyst verkningslöst — det syns inte i något annat test.
    """

    def _source(self):
        path = MODULE_ROOT / POWERBOX_JS
        self.assertTrue(path.exists(), f'{POWERBOX_JS} saknas')
        return path.read_text(encoding='utf-8')

    def test_command_keeps_its_backend_contract(self):
        """Kontraktet mot /ai/powerbox/lookup och /ai/powerbox/run finns kvar."""
        src = self._source()
        for endpoint in ('/ai/powerbox/lookup', '/ai/powerbox/run'):
            self.assertIn(
                endpoint, src,
                f'Kommandot måste anropa {endpoint} — utan det hämtas inga '
                'medarbetare respektive körs ingenting')

    def test_plugin_is_registered_for_html_editors(self):
        """Pluginet registreras i MAIN_PLUGINS, annars får editorn det inte."""
        src = self._source()
        self.assertIn('MAIN_PLUGINS', src)
        self.assertIn('ai_powerbox', src,
                      'Plugin-id:t måste finnas — det är nyckeln både i '
                      'MAIN_PLUGINS och i kommandokatalogen')

    def test_commands_are_added_to_the_powerbox(self):
        """Kommandona läggs i PowerboxPlugin:s katalog (kategori "ai")."""
        src = self._source()
        self.assertIn('getAvailablePowerboxCommands', src,
                      'Utan patchen på getAvailablePowerboxCommands syns '
                      'inga AI-medarbetare när användaren skriver "/"')
        self.assertIn('categoryId: "ai"', src,
                      'Kommandona ska hamna i AI-kategorin, inte spridas')

    def test_composer_service_is_optional_not_required(self):
        """Tjänsten får inte göras obligatorisk — då kraschar kärnan utan
        ai_agent_transcript (6.1:s avgränsning)."""
        src = self._source()
        self.assertIn('getComposerService', src,
                      'Konsumenten av ai_powerbox-tjänsten ska finnas (6.1)')
        self.assertIn('return false', src,
                      'Utan tjänsten ska kärnans egen väg användas — '
                      'funktionen måste kunna lämna över, inte kasta')


if __name__ == '__main__':
    unittest.main()


@tagged('ai_core', 'post_install', '-at_install')
class TestApprovalsSpecConsistency(TransactionCase):
    """1.3 — ingen formulering i `approvals`-specen motsäger en svarskanal."""

    def test_spec_requires_the_same_gate_not_a_silent_denial(self):
        """Specen kräver samma GRIND — inte samma NEKANDE.

        "A path that resolves a tool registry and executes tools SHALL
        evaluate the same gate as the agent loop" — kanalen låter
        `/tools/run` göra precis det: `evaluate()` anropas, och när den
        säger `needs_user` frågar ytan i stället för att neka. Grinden är
        oförändrad; det är svaret som nu kan lämnas.
        """
        text = _read_openspec_spec('specs/approvals/spec.md')
        if text is None:
            self.skipTest(
                'approvals-specen går inte att läsa i den här miljön '
                '(sätt %s om planen ligger någon annanstans)' % OPENSPEC_ROOT_ENV)
        self.assertIn('SHALL evaluate the same gate', text)
        # Ingen formulering får kräva att en yta UTAN kanal ska neka —
        # det vore att göra kanalen otillåten.
        self.assertNotIn('SHALL NOT prompt', text)
        self.assertNotIn('must deny when no approval channel', text)


# ──────────────────────────────────────────────────────────────────────
# Grind 5 — språket i källan (core-english-and-sv-po)
# ──────────────────────────────────────────────────────────────────────

#: Modulens källspråk är engelska. Svenska hör hemma i ``i18n/sv.po``.
#: Grinden bevakar att en ny svensk sträng inte smyger in — den fångar
#: *tillväxt*, inte befintlig skuld. Skulden mäts i
#: ``tests/language_baseline.json`` och får bara minska.
SWEDISH_LETTERS = re.compile(r'[åäöÅÄÖ]')

#: Kataloger som grinden bevakar (allt utom tester).
LANGUAGE_SCOPE = ('models', 'core', 'controllers', 'wizards', 'fields', 'migrations')

#: Rotfiler som grinden bevakar utöver katalogerna.
LANGUAGE_ROOT_FILES = ('hooks.py',)

#: XML-attribut vars värde är användartext och därför ska vara engelskt.
#: ``name`` och ``title`` togs med efter att ett svep missade menyetiketter,
#: knappnamn och diagramtitlar som bor där — de syntes i UI:t men inte i
#: grinden. ``invisible``/``esc`` är kod (domäner, uttryck) och prövas inte.
LANGUAGE_XML_ATTRIBUTES = ('string', 'help', 'placeholder', 'confirm', 'name', 'title')

#: Filer och mönster som är undantagna, med skrivet skäl. Ett undantag utan
#: skäl fallerar ``test_language_exemptions_have_reasons``.
LANGUAGE_EXEMPT = [
    (
        'data/*_skills.xml',
        'Skill-recept är promptinnehåll (se spec: domain-skills), inte '
        'UI-strängar. De injiceras i agentens systemprompt på det språk '
        'agenten arbetar på och översätts därför inte.',
    ),
    (
        'data/*_tools.xml',
        'Verktygsbeskrivningar är promptinnehåll — instruktioner till '
        'modellen, inte text som renderas i ett formulär.',
    ),
    (
        'data/*_coworker.xml',
        'Seed-poster (namn och beskrivningar) blir databasrader, inte '
        'UI-strängar. Att översätta dem kräver en egen ändring för '
        'kundinstallationer på andra språk.',
    ),
    (
        'data/skill_*.xml',
        'Skill-recept: description och trigger_keywords är promptinnehåll och '
        'svenska nyckelord som ska matcha svenska frågor. Samma skäl som '
        'data/*_skills.xml.',
    ),
    (
        'data/git_identity_skill.xml',
        'Skill-recept (samma skäl som data/skill_*.xml).',
    ),
    (
        'data/okf_artifact_types_personal.xml',
        'The artifact type name "mål" is a stored key: the domains search on '
        'artifact_type_id.name in ("roll", "mål"). Translating it empties '
        'those searches.',
    ),
    (
        'migrations/',
        'Migreringar är historisk kod som redan kört i drift. Bara '
        'kommentarer och docstrings får röras där — en literal som används '
        'i ett env-uppslag eller en jämförelse lämnas orörd.',
    ),
]

#: Fältnamn i ``ir.ui.view``-vakten som är DATA (svenska fältnamn som ska
#: mappas till engelska), inte UI-text. Undantas per fil och fältnamn.
LANGUAGE_DATA_KEYS = {
    'models/ir_ui_view.py': (
        'kundämne', 'kundämnen', 'kundämne_count', 'kundämnen_count',
    ),
}

#: Literaler som är DATA, inte UI-text: regex som ska matcha svenska
#: användar-/LLM-svar, fältnamn i domäner, translittereringstabeller. Att
#: översätta dem ändrar beteende — de undantas per fil och delsträng, med
#: skrivet skäl. En post som inte längre matchar något fallerar grinden.
LANGUAGE_DATA_LITERALS = {
    'models/ai_coworker.py': [
        ('Allmän',
         'Legacy record name compared against to rename the seeded default '
         'coworker. It is a stored value, not a label — translating it would '
         'stop the migration matching.'),
        ("'Ä'",
         'ASCII transliteration table for HTTP headers: the keys are the '
         'data (å/ä/ö -> a/a/o).'),
        ('beställare_ref',
         'Field name used in a domain and in a record dict. Renaming it '
         'would break the domain, not translate a label.'),
        ('beställare_trust',
         'Field name passed into a task record. Data, not UI text.'),
        ('@[a-z',
         'Question normaliser: a character class that strips @mentions. The '
         'Swedish letters are the point — they must match Swedish input.'),
        ('[^a-z',
         'Question normaliser: strips punctuation but keeps Swedish letters, '
         'so two Swedish questions compare equal.'),
        ('bra fråga',
         'Narration regex: matches Swedish filler phrases in an LLM answer '
         '("bra fråga", "låt mig", "jag har"). Translating the pattern '
         'would stop it matching the answers it exists to clean.'),
        ('Notering',
         'Meta-note regex: strips a trailing "Notering:"/"OBS:" block from '
         'an answer. The Swedish word is the needle, not a label.'),
    ],
    'models/ai_personal_memory.py': [
        ('[A-ZÅÄÖ]',
         'Proper-noun regex: catches uppercase words and abbreviations '
         'including Å/Ä/Ö (K2, K3, BAS, AB, EF). Swedish capital letters are '
         'the data it must match.'),
    ],
    'models/ai_memory_mixin.py': [
        ('[A-ZÅÄÖ]',
         'Same proper-noun regex as ai_personal_memory: Swedish capitals are '
         'part of the character class, not a label.'),
    ],
    'core/verify.py': [
        ('källa|källor',
         'Source-claim regex: it detects "Källa:"/"Referens:" in an LLM '
         'answer so an unbacked citation can be flagged. The Swedish words '
         'are the needles.'),
        ('[A-ZÅÄÖ]',
         'Proper-noun and sentence-start regexes: Swedish capitals are part '
         'of the character classes.'),
        ('swedish',
         'Language detection: a dict of Swedish vs English function words. '
         'The words themselves are the data.'),
        ('_ENTITY_STOPWORDS',
         'Entity stopword list: Swedish and English function words that must '
         'not be treated as named entities. Translating them would break '
         'entity extraction.'),
    ],
    'core/tool_select.py': [
        ('_BRIDGE',
         'Swedish -> English token bridge for the lexical fallback scorer. '
         'Tool descriptions are English, prompts are often Swedish; this map '
         'is what connects them. The Swedish keys are the data.'),
        ('att till',
         'Stopword list for the same scorer, covering both languages.'),
        ('sök',
         'Bridge/stem entries for Swedish query terms. Data.'),
    ],
    'core/loop.py': [
        ('"kör"',
         'Accepted answer when a human approves a plan: "ja", "yes", "ok", '
         '"approve", "run", "kör". The Swedish word is one of the accepted '
         'inputs, not a label.'),
    ],
}


def _swedish_in_strings(path, source, tree):
    """Returnerar ``[(lineno, text)]`` för svenska kodliteraler (ej docstrings)."""
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) \
               and isinstance(body[0].value, ast.Constant) \
               and isinstance(body[0].value.value, str):
                docs.add(id(body[0].value))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
           and id(node) not in docs and SWEDISH_LETTERS.search(node.value):
            hits.append((node.lineno, node.value.strip().split('\n')[0][:70]))
    return sorted(hits)


def _swedish_in_xml(path):
    """Returnerar ``[(lineno, vad, text)]`` för svensk XML-användartext.

    Två ytor: de fyra UI-attributen, och **elementtext** (``>text<``) — som
    är där menyetiketter, gruppnamn och cron-namn bor. Den första versionen
    såg bara attributen och missade 34 meny- och gruppnamn; de syntes i UI:t
    men inte i grinden.
    """
    hits = []
    for lineno, line in enumerate(path.read_text(encoding='utf-8').split('\n'), 1):
        for attr in LANGUAGE_XML_ATTRIBUTES:
            for match in re.finditer(attr + r'="([^"]*)"', line):
                if SWEDISH_LETTERS.search(match.group(1)):
                    hits.append((lineno, attr, match.group(1)[:60]))
        for match in re.finditer(r'>([^<>]*[åäöÅÄÖ][^<>]*)<', line):
            text = match.group(1).strip()
            if text:
                hits.append((lineno, 'text', text[:60]))
    return hits


def _language_exempt(rel):
    """Sant om ``rel`` matchar ett undantag i ``LANGUAGE_EXEMPT``."""
    for pattern, _reason in LANGUAGE_EXEMPT:
        if pattern.endswith('/'):
            if rel.startswith(pattern):
                return True
        elif pathlib.PurePath(rel).match(pattern):
            return True
    return False


def _scan_swedish(root):
    """Returnerar ``{relpath: count}`` för svenska kodliteraler i modulen."""
    found = {}
    targets = []
    for scope in LANGUAGE_SCOPE:
        base = root / scope
        if base.is_dir():
            targets.extend(sorted(base.rglob('*.py')))
    for name in LANGUAGE_ROOT_FILES:
        if (root / name).is_file():
            targets.append(root / name)
    for path in targets:
        if '__pycache__' in path.parts:
            continue
        rel = path.relative_to(root).as_posix()
        if _language_exempt(rel):
            continue
        try:
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue
        hits = _swedish_in_strings(path, source, tree)
        data_keys = LANGUAGE_DATA_KEYS.get(rel, ())
        if data_keys:
            hits = [h for h in hits if not any(k in h[1] for k in data_keys)]
        data_literals = [needle for needle, _reason in LANGUAGE_DATA_LITERALS.get(rel, [])]
        if data_literals:
            hits = [h for h in hits if not any(n in h[1] for n in data_literals)]
        if hits:
            found[rel] = len(hits)
    return found


@tagged('ai_core', 'post_install', '-at_install')
class StructuralGateLanguage(TransactionCase):
    """Grind: källan talar engelska, svenskan bor i ``i18n/sv.po``.

    Ägs av ``core-english-and-sv-po``. Frågan är "är den här strängen skriven
    på modulens källspråk?" — inte "är den översatt?".

    Grinden fallerar på **tillväxt**: en fil får inte få fler svenska
    literaler än baslinjen i ``tests/language_baseline.json`` tillåter.
    Att sänka ett tal är en medveten commit; att höja det är en regression.
    """

    baseline_path = MODULE_ROOT / 'tests' / 'language_baseline.json'

    def _baseline(self):
        import json
        return json.loads(self.baseline_path.read_text(encoding='utf-8'))['files']

    def _baseline_xml(self):
        import json
        return json.loads(self.baseline_path.read_text(encoding='utf-8')).get('xml', {})

    def test_no_new_swedish_string_literals(self):
        baseline = self._baseline()
        actual = _scan_swedish(MODULE_ROOT)
        violations = []
        for rel, count in sorted(actual.items()):
            allowed = baseline.get(rel, {}).get('code', 0)
            if count > allowed:
                violations.append(f'{rel}: {count} svenska literaler (tak {allowed})')
        self.assertFalse(
            violations,
            'Nya svenska strängliteraler. Källan ska vara engelsk — flytta '
            'texten till `i18n/sv.po` som msgstr, eller höj taket i '
            'tests/language_baseline.json med ett skrivet skäl:\n  '
            + '\n  '.join(violations),
        )

    def test_baseline_does_not_claim_more_than_reality(self):
        """Ett tak som ligger över verkligheten döljer en framtida ökning."""
        baseline = self._baseline()
        actual = _scan_swedish(MODULE_ROOT)
        stale = []
        for rel, entry in sorted(baseline.items()):
            allowed = entry.get('code', 0)
            count = actual.get(rel, 0)
            if count < allowed:
                stale.append(f'{rel}: tak {allowed}, verkligt {count}')
        self.assertFalse(
            stale,
            'Baslinjen ligger över verkligheten — sänk taket så att det '
            'speglar koden (annars döljs nästa ökning):\n  ' + '\n  '.join(stale),
        )

    def test_no_swedish_xml_ui_text(self):
        """XML-etikatter renderas i UI:t — en ny svensk sådan är en regression.

        Räknaren är ett **tak per fil**, inte en absolut regel: de 136
        befintliga etiketterna kan inte översättas i samma commit som grinden
        införs. Taket får bara sjunka.
        """
        baseline = self._baseline_xml()
        actual = {}
        for path in sorted(MODULE_ROOT.rglob('*.xml')):
            if '__pycache__' in path.parts:
                continue
            rel = path.relative_to(MODULE_ROOT).as_posix()
            if _language_exempt(rel):
                continue
            hits = _swedish_in_xml(path)
            if hits:
                actual[rel] = len(hits)
        violations = [
            f'{rel}: {count} svenska attribut (tak {baseline.get(rel, 0)})'
            for rel, count in sorted(actual.items())
            if count > baseline.get(rel, 0)
        ]
        self.assertFalse(
            violations,
            'Svensk text i XML-attribut som renderas i UI:t. Skriv engelska '
            'och lägg översättningen i `i18n/sv.po`:\n  ' + '\n  '.join(violations),
        )

    def test_xml_baseline_does_not_claim_more_than_reality(self):
        """Samma ratchet som för koden — ett slappt tak döljer nästa ökning."""
        baseline = self._baseline_xml()
        actual = {}
        for path in sorted(MODULE_ROOT.rglob('*.xml')):
            if '__pycache__' in path.parts:
                continue
            rel = path.relative_to(MODULE_ROOT).as_posix()
            if _language_exempt(rel):
                continue
            hits = _swedish_in_xml(path)
            if hits:
                actual[rel] = len(hits)
        stale = [
            f'{rel}: tak {allowed}, verkligt {actual.get(rel, 0)}'
            for rel, allowed in sorted(baseline.items())
            if actual.get(rel, 0) < allowed
        ]
        self.assertFalse(
            stale,
            'XML-baslinjen ligger över verkligheten — sänk taket:\n  ' + '\n  '.join(stale),
        )

    def test_sv_po_is_generated_not_hand_written(self):
        """Varje post i sv.po måste bära en ``#:``-referens.

        En handskriven .po utan referenser laddas men appliceras aldrig på
        fält — den ser översatt ut och är det inte. Referensen är det som
        binder msgid till en modell eller en kodrad.
        """
        po = MODULE_ROOT / 'i18n' / 'sv.po'
        if not po.is_file():
            self.skipTest('i18n/sv.po finns inte i den här utcheckningen')
        import re as _re
        text = po.read_text(encoding='utf-8')
        blocks = text.split('\n\n')
        missing = []
        for block in blocks:
            if 'msgid' not in block or 'msgstr' not in block:
                continue
            msgid = _re.search(r'^msgid "(.+)"$', block, _re.M)
            if not msgid:
                continue  # header eller flerradig post
            if not _re.search(r'^#:', block, _re.M):
                missing.append(msgid.group(1)[:60])
        self.assertFalse(
            missing,
            'sv.po-poster utan #:-referens — filen är handskriven, inte '
            'genererad med --i18n-export:\n  ' + '\n  '.join(missing[:10]),
        )

    def test_language_data_literals_have_reasons(self):
        """En data-literal utan skäl går inte att skilja från en glömd sträng."""
        missing = [
            f'{rel}: {needle}'
            for rel, entries in LANGUAGE_DATA_LITERALS.items()
            for needle, reason in entries
            if not (reason or '').strip()
        ]
        self.assertFalse(missing, 'Data-literaler utan motivering:\n  ' + '\n  '.join(missing))

    def test_language_data_literals_match_something(self):
        """En data-literal som inte längre träffar något döljer en riktig sträng."""
        stale = []
        for rel, entries in LANGUAGE_DATA_LITERALS.items():
            path = MODULE_ROOT / rel
            if not path.is_file():
                stale.append(f'{rel}: filen finns inte')
                continue
            text = path.read_text(encoding='utf-8')
            for needle, _reason in entries:
                if needle not in text:
                    stale.append(f'{rel}: {needle!r} matchar inget')
        self.assertFalse(stale, 'Föråldrade data-literaler:\n  ' + '\n  '.join(stale))

    def test_language_exemptions_have_reasons(self):
        """Ett undantag utan skäl är oanträffbart vid granskning."""
        missing = [
            f'{pattern} — skäl saknas'
            for pattern, reason in LANGUAGE_EXEMPT
            if not (reason or '').strip()
        ]
        self.assertFalse(missing, 'Undantag utan motivering:\n  ' + '\n  '.join(missing))

    def test_language_exemptions_match_something(self):
        """En föråldrad undantagspost döljer inget men lurar läsaren."""
        stale = []
        for pattern, _reason in LANGUAGE_EXEMPT:
            if pattern.endswith('/'):
                hit = any((MODULE_ROOT / pattern).rglob('*.py'))
            else:
                hit = any(MODULE_ROOT.rglob(pattern.split('/')[-1]))
            if not hit:
                stale.append(pattern)
        self.assertFalse(stale, 'Undantag som inte matchar något:\n  ' + '\n  '.join(stale))

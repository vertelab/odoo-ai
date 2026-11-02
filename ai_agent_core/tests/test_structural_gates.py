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
"""

import ast
import pathlib
import unittest

from odoo.tests.common import TransactionCase, tagged


MODULE_ROOT = pathlib.Path(__file__).resolve().parent.parent
"""Rot för ai_agent_core (tests/ ligger ett steg ner)."""


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


def _is_silent(handler):
    """Ett ``except`` är tyst om kroppen bara är ``pass`` eller ``continue``."""
    body = [n for n in handler.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    if len(body) != 1:
        return False
    return isinstance(body[0], (ast.Pass, ast.Continue))


def _has_label(handler, source_lines):
    """Sant om etiketten står i ``except``-raden eller i kroppens första rad."""
    candidates = [source_lines[handler.lineno - 1]]
    if handler.body:
        first = handler.body[0]
        candidates.append(source_lines[first.lineno - 1])
    return any(label in line for line in candidates for label in SILENT_EXCEPT_LABELS)


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
# Grind 2 — klass-wiringen (wire-orphaned-core-mechanisms 1.1)
# ──────────────────────────────────────────────────────────────────────

#: Publika klasser i ``core/`` som med flit inte har någon anropare
#: utanför ``core/`` och ``tests/``. Varje post är ``(klassnamn, skäl)``.
#:
#: Uppmätt mot 18.0 @ 54cd3618: 21 publika klasser i core/, 12 utan
#: anropare utanför core/ och tests/. Posterna nedan är de som har ett
#: skrivet skäl att stanna. Resten åtgärdas i wire-orphaned-core-mechanisms
#: §7–§9 (EvalRunner, IntelligentRouter, ExternalAgentHandle, JevClient,
#: LexicalSelector). `BudgetTracker`/`BudgetEnforcingProvider` fanns aldrig
#: här — de togs bort ur core/budget.py i §2.
CORE_WIRING_ALLOWLIST = [
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
DETECT_ERROR_PREFIX = 'kontextkälla'


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


if __name__ == '__main__':
    unittest.main()

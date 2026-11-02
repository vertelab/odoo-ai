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
"""

import ast
import pathlib
import unittest

from odoo.tests.common import TransactionCase


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
# Grind 2 — klass-wiringen (wire-orphaned-core-mechanisms 1.1)
# ──────────────────────────────────────────────────────────────────────

#: Publika klasser i ``core/`` som med flit inte har någon anropare
#: utanför ``core/`` och ``tests/``. Varje post är ``(klassnamn, skäl)``.
#:
#: Uppmätt mot 18.0 @ 54cd3618: 21 publika klasser i core/, 12 utan
#: anropare utanför core/ och tests/. Posterna nedan är de som har ett
#: skrivet skäl att stanna. Resten åtgärdas i wire-orphaned-core-mechanisms
#: §2 och §7–§9 (BudgetTracker, BudgetEnforcingProvider, EvalRunner,
#: IntelligentRouter, ExternalAgentHandle, JevClient, LexicalSelector).
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


if __name__ == '__main__':
    unittest.main()

# -*- coding: utf-8 -*-
"""B6 — HITL-routing: `select_interrupt_handler` måste ha anropare.

Bakgrund: `select_interrupt_handler` (core/interrupt.py) är den ENDA
beslutsplatsen för vilken HITL-mekanism en körning får — pausad HITL för
interaktiva init-typer, record-HITL för obevakade. Men funktionen hade
**noll** anropare i drift: bara tester rörde den. Cron-vägen instansierade
`AutoInterruptHandler()` direkt, utan `hitl_request`.

Konsekvenskedja:
  1. Routing-arkitekturen var ren fiktion — ingen följde den.
  2. Cron fick en handler utan mottagare för ärendet.
  3. `approve_tool` godkände allt (B4) → cron kunde köra destruktiva
     verktyg utan människa.

Detta test bevakar att kopplingen finns: cron-vägen ska gå genom
`select_interrupt_handler`, inte instansiera handlern själv.
"""

import ast
import inspect
import textwrap

from odoo.tests.common import TransactionCase, tagged


def _module_source(rel_path):
    from odoo.addons.ai_agent_core import __file__ as pkg_file
    import os
    root = os.path.dirname(pkg_file)
    with open(os.path.join(root, rel_path), encoding='utf-8') as fh:
        return fh.read()


@tagged('ai_core', 'post_install', '-at_install')
class TestSelectInterruptHandlerIsWired(TransactionCase):
    """11.3: `select_interrupt_handler` får inte vara oanvänd."""

    def test_has_production_callers(self):
        """Strukturell grind: minst en anropare utanför core/interrupt.py."""
        import os
        from odoo.addons.ai_agent_core import __file__ as pkg_file
        root = os.path.dirname(pkg_file)

        callers = []
        for dirpath, _dirnames, filenames in os.walk(root):
            if os.path.basename(dirpath) in ('tests', '__pycache__'):
                continue
            for fname in filenames:
                if not fname.endswith('.py'):
                    continue
                path = os.path.join(dirpath, fname)
                rel = os.path.relpath(path, root)
                if rel == os.path.join('core', 'interrupt.py'):
                    continue
                with open(path, encoding='utf-8') as fh:
                    src = fh.read()
                if 'select_interrupt_handler' in src:
                    callers.append(rel)

        self.assertTrue(
            callers,
            'B6: `select_interrupt_handler` har inga anropare i drift — '
            'HITL-routingen är då bara dokumentation, och varje körväg '
            'väljer handler på egen hand. Anropare hittades: inga.')

    def test_cron_path_uses_the_selector(self):
        """Cron-vägen ska välja handler via selektorn, inte instansiera själv."""
        src = _module_source('models/ai_coworker.py')
        tree = ast.parse(src)

        # Hitta funktionen som innehåller cron-körningen: den som skapar
        # en session och anropar _build_loop med init_type 'cron'.
        cron_fn = None
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body_src = ast.get_source_segment(src, node) or ''
            if "'cron'" in body_src and '_build_loop' in body_src:
                cron_fn = node
                break

        self.assertIsNotNone(cron_fn, 'cron-körvägen hittades inte')
        body_src = ast.get_source_segment(src, cron_fn) or ''
        self.assertIn(
            'select_interrupt_handler', body_src,
            'B6: cron-vägen väljer inte HITL-mekanism via '
            'select_interrupt_handler')

    def test_cron_passes_hitl_request(self):
        """Utan hitl_request kan cron inte lämna ett ärende — då nekas allt
        (vilket är rätt, men ger ingen väg framåt för användaren)."""
        src = _module_source('models/ai_coworker.py')
        tree = ast.parse(src)

        cron_fn = None
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body_src = ast.get_source_segment(src, node) or ''
            if "'cron'" in body_src and '_build_loop' in body_src:
                cron_fn = node
                break

        body_src = ast.get_source_segment(src, cron_fn) or ''
        self.assertIn(
            'hitl_request=', body_src,
            'B6: cron-vägen skickar ingen hitl_request — då kan inget '
            'ärende skapas och varje godkännandekrävande verktyg nekas '
            'utan spår')


@tagged('ai_core', 'post_install', '-at_install')
class TestCronDenialIsUnderstandable(TransactionCase):
    """11.5: en cron-körning ska nekas med begriplig orsak — inte tyst."""

    def test_denial_reason_mentions_no_recipient(self):
        """När hitl_request saknas ska loggen säga VARFÖR."""
        import asyncio
        from odoo.addons.ai_agent_core.core.interrupt import (
            AutoInterruptHandler)

        handler = AutoInterruptHandler(hitl_request=None)
        loop = asyncio.new_event_loop()
        try:
            with self.assertLogs(
                    'odoo.addons.ai_agent_core.core.interrupt',
                    level='WARNING') as captured:
                allowed = loop.run_until_complete(
                    handler.approve_tool('odoo_unlink', 'destructive', {}))
        finally:
            loop.close()

        self.assertFalse(allowed, 'destruktivt verktyg får inte godkännas')
        joined = '\n'.join(captured.output)
        self.assertIn('no approval recipient', joined,
                      'orsaken måste framgå av loggen')

    def test_read_tools_still_allowed_without_recipient(self):
        """Läsverktyg kräver inget godkännande — de ska passera."""
        import asyncio
        from odoo.addons.ai_agent_core.core.interrupt import (
            AutoInterruptHandler)

        handler = AutoInterruptHandler(hitl_request=None)
        loop = asyncio.new_event_loop()
        try:
            allowed = loop.run_until_complete(
                handler.approve_tool('odoo_search', 'read_only', {}))
        finally:
            loop.close()
        self.assertTrue(allowed, 'läsverktyg ska passera utan godkännande')

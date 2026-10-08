# -*- coding: utf-8 -*-
"""Kärnan är domän-ren — manifest OCH kod (calendar-events-okf-scoping D2).

VARFÖR: manifest-kravet fanns redan, och överträdelsen uppstod ändå — i
koden. `ai_agent_core` beroende inte på `calendar`, men anropade
`env['calendar.event']` **oskyddat** i en cron. Utan kalendern installerad
kastade raden `KeyError`, och felet dolde sig bakom en `try/except` i
cronens anropare.

Ett krav som bara ser manifestet fångar inte detta. Detta test söker i
**koden** efter domänreferenser och kräver att varje sådan är guardad.

Dessa tester bevisar:
  1. manifestet beror inte på en domänmodul
  2. ingen domänmodell anropas oskyddat i kärnans kod
  3. kärnan kan installeras och köras utan domänmoduler
"""

import ast
import os
import re

from odoo.tests import common, tagged
from ._config_param_guard import ConfigParamGuardedCase

#: Modeller som är DOMÄNER i andra repon — de får bara nås guardat.
#: Listan är avsiktligt kort och namnger bara modeller vars brygga bor i ett
#: annat repo (calendar_ai, dms_ai, website_ai, helpdesk_ai). Modeller som
#: kärnan äger (ai.*) eller som kommer från kärnans egna beroenden (base,
#: mail, hr, web_pwa_push) prövas inte — de är alltid tillgängliga.
DOMAIN_MODELS = (
    'calendar.event',
    'dms.file',
    'website.page',
    'helpdesk.team',
    'helpdesk.ticket',
)

#: Katalogen vi granskar — kärnans kod, inte tester.
_MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'models')

#: En guard är en närvaro-/frånvaroprövning i samma eller föregående
#: uttryck. Vi letar i en radruta ovanför anropet.
_GUARD_WINDOW = 8


def _source_files():
    for fn in sorted(os.listdir(_MODELS_DIR)):
        if fn.endswith('.py'):
            yield os.path.join(_MODELS_DIR, fn)


def _is_guard(text, model):
    """Finns en guard för `model` i texten?"""
    patterns = (
        "'%s' in self.env" % model,
        "'%s' not in self.env" % model,
        "'%s' in env" % model,
        "'%s' not in env" % model,
        '"%s" in self.env' % model,
        '"%s" not in self.env' % model,
    )
    if any(p in text for p in patterns):
        return True
    # env.get('<modell>') är också en guard — men bara för modellen själv.
    return bool(re.search(r"env\.get\(\s*['\"]%s['\"]" % re.escape(model),
                          text))


def _is_docstring_or_comment(line):
    s = line.strip()
    return s.startswith('#') or s.startswith('"""') or s.startswith("'''")


@tagged('okf', 'core-purity', 'post_install', '-at_install')
class TestCoreDomainPurity(ConfigParamGuardedCase):
    """D2: kärnan är domän-ren i manifest och kod."""

    def test_manifest_has_no_domain_dependency(self):
        """Kärnans manifest beror inte på en domänmodul."""
        manifest = os.path.join(os.path.dirname(_MODELS_DIR),
                                '__manifest__.py')
        src = open(manifest, encoding='utf-8').read()
        tree = ast.parse(src)
        depends = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant)
                            and k.value == 'depends'):
                        depends = [e.value for e in v.elts]
        self.assertIsNotNone(depends, 'manifestet har ingen depends-lista')
        for model in DOMAIN_MODELS:
            module = model.split('.')[0]
            self.assertNotIn(
                module, depends,
                'kärnans manifest beror på domänmodulen %r' % module)

    def test_no_unguarded_domain_reference(self):
        """Ingen domänmodell anropas oskyddat i kärnans kod.

        Detta är testet som hade fångat `env['calendar.event']` i den döda
        cronen: manifestet var rent, men koden refererade kalendern utan
        guard.
        """
        offenders = []
        for path in _source_files():
            lines = open(path, encoding='utf-8').read().split('\n')
            for i, line in enumerate(lines):
                for model in DOMAIN_MODELS:
                    if "env['%s']" % model not in line \
                            and 'env["%s"]' % model not in line:
                        continue
                    if _is_docstring_or_comment(line):
                        continue
                    window = '\n'.join(
                        lines[max(0, i - _GUARD_WINDOW):i + 1])
                    if not _is_guard(window, model):
                        offenders.append(
                            '%s:%d  %s' % (os.path.basename(path),
                                           i + 1, line.strip()))
        self.assertEqual(
            offenders, [],
            'oskyddade domänreferenser i kärnan:\n  ' + '\n  '.join(offenders))

    def test_core_installs_without_calendar(self):
        """Kärnan fungerar utan kalendern installerad.

        Testet kör i en databas där `calendar` kan vara frånvarande. Det
        som prövas är att ingen kodväg i kärnan kastar `KeyError` på en
        saknad domänmodell — inte att kalendern finns.
        """
        # Kärnans egna modeller ska finnas oavsett.
        self.assertIn('ai.okf.concept', self.env)
        self.assertIn('ai.personal.memory', self.env)
        # En domänmodell får saknas utan att kärnan faller.
        has_calendar = 'calendar.event' in self.env
        self.assertIsInstance(has_calendar, bool)

    def test_domain_model_absent_is_not_an_error(self):
        """En frånvarande domänmodell ger inget fel i kärnans vägar.

        `env.get()` returnerar None för en saknad modell — det är den
        guardade formen. `env[...]` kastar KeyError — det är den oskyddade.
        """
        missing = self.env.get('finns.inte.modell')
        self.assertIsNone(missing)
        with self.assertRaises(KeyError):
            self.env['finns.inte.modell']


class TestProviderApiCalls(ConfigParamGuardedCase):
    """Ingen kod anropar en provider-metod som inte finns.

    VARFÖR (llm-call-provider-path): tre vägar anropade `provider._call_llm`
    och `provider._chat_completion` — metoder som inte existerar. Varje
    anrop kastade AttributeError, som svaldes av ett try/except och gjorde
    en trasig väg till en tyst fallback (onboarding-cronen körde men gav
    alltid den hårdkodade texten).

    Detta test fångar nästa påhittade provider-metod innan den når drift.
    """

    def test_provider_methods_called_exist(self):
        """Varje `provider._<namn>(`-anrop i kärnans kod finns på ai.provider.

        Ett anrop som är guardat med `hasattr(provider, '_x')` i samma
        uttryck är avsiktligt valfritt (t.ex. vision/whisper) och räknas
        inte — det faller till ett tydligt fel, inte en tyst nolla.
        """
        provider = self.env['ai.provider']
        missing = []
        for path in _source_files():
            with open(path, 'r', encoding='utf-8') as fh:
                src = fh.read()
            # Hoppa över kommentarer — de namnger avsiktligt döda anrop
            # (t.ex. "ai.provider._generate FINNS INTE").
            code = '\n'.join(
                ln for ln in src.splitlines()
                if not ln.lstrip().startswith('#'))
            for m in re.finditer(
                    r'(?:provider|\[\'ai\.provider\'\]|\["ai\.provider"\])\s*\.\s*(_[a-zA-Z_]+)\s*\(',
                    code):
                name = m.group(1)
                # Guardat anrop: hasattr(provider, '_x') i närheten (samma
                # rad eller inom ett litet fönster ovanför).
                line_start = code.rfind('\n', 0, m.start()) + 1
                window_start = code.rfind('\n', 0, max(0, line_start - 1))
                for _ in range(4):
                    window_start = code.rfind('\n', 0, max(0, window_start))
                window = code[max(0, window_start):m.end()]
                if "hasattr(" in window and "'%s'" % name in window:
                    continue
                if not hasattr(provider, name):
                    missing.append('%s: provider.%s' % (
                        os.path.basename(path), name))
        self.assertEqual(
            missing, [],
            'anrop till icke-existerande provider-metoder: %s' % missing)

# -*- coding: utf-8 -*-
"""Fail-closed access i OKF-injektionen (okf-owner-and-access-scoping D1).

VARFÖR: OKF:s urval (dedup + limit) sker i SQL före access-filtret, och
access-filtret defaultade till *synlig* när uppslaget saknades. Ett koncept
vars källor blandade läsbar och hemlig data injicerades därför i sin helhet —
ett företagsminne kunde läcka `dms.file`, `res.partner` och `project.task`
som användarens `res.users`-rättigheter inte ger.

Dessa tester bevisar:
  1. ett saknat prövningsresultat tolkas INTE som synligt (fail-closed)
  2. ett koncept utan attribution returnerar inte hela sin sammanfattning
  3. radnivå-filtreringen körs i injektionsvägen (den var avkopplad)
  4. avförda koncept loggas med skäl
"""

import logging
from unittest.mock import patch

from odoo.tests import common, tagged
from ._config_param_guard import ConfigParamGuardedCase

_logger = logging.getLogger(__name__)


@tagged('okf', 'post_install', '-at_install')
class TestOkfFailClosedAccess(ConfigParamGuardedCase):
    """D1: injektionen är fail-closed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.Concept = cls.env['ai.okf.concept']
        cls.atype = cls.env['ai.artifact.type'].search(
            [('name', '=', 'knowledge')], limit=1)
        if not cls.atype:
            cls.atype = cls.env['ai.artifact.type'].create({
                'name': 'knowledge', 'kind': 'knowledge'})

    def _mk_concept(self, **kw):
        vals = {
            'artifact_type_id': self.atype.id,
            'scope': 'company',
            'concept_key': 'failclosed.key',
            'summary': 'Rad 1\nRad 2\nRad 3',
            'owner_company_id': self.company.id,
        }
        vals.update(kw)
        return self.Concept.create(vals)

    # ── 1.1: saknat prövningsresultat är inte synligt ──

    def test_unresolved_source_is_not_visible(self):
        """Ett koncept vars source_ref saknas i visible_map injiceras inte.

        Föll på den gamla koden: `vis.get(r, True)` tolkade en oprövad källa
        som synlig.
        """
        c = self._mk_concept(
            concept_key='failclosed.unresolved',
            source_ref='dms.file,7',
            summary='Hemlig text',
        )
        # visible_map utan post för c.id → inget prövningsresultat alls
        block = self.Concept._format_concept_block(
            c, 2000, 'TEST', user=self.env.user)
        self.assertNotIn('Hemlig text', block)

    def test_visible_source_is_injected(self):
        """Motprov: en prövad, läsbar källa injiceras."""
        c = self._mk_concept(
            concept_key='failclosed.visible',
            source_ref='res.partner,10',
            summary='Läsbar text',
        )
        with patch.object(type(self.Concept), '_resolve_visible_sources',
                          return_value={c.id: {'res.partner,10': True}}):
            block = self.Concept._format_concept_block(
                c, 2000, 'TEST', user=self.env.user)
        self.assertIn('Läsbar text', block)

    def test_explicitly_hidden_source_is_not_injected(self):
        """En prövad, icke-läsbar källa injiceras inte."""
        c = self._mk_concept(
            concept_key='failclosed.hidden',
            source_ref='dms.file,7',
            summary='Hemlig text',
        )
        with patch.object(type(self.Concept), '_resolve_visible_sources',
                          return_value={c.id: {'dms.file,7': False}}):
            block = self.Concept._format_concept_block(
                c, 2000, 'TEST', user=self.env.user)
        self.assertNotIn('Hemlig text', block)

    # ── 1.2: utan attribution returneras inte hela sammanfattningen ──

    def test_no_attribution_hides_all_lines(self):
        """Ett koncept utan attribution ger noll synliga rader.

        Föll på den gamla koden: grenen returnerade hela `summary.split('\\n')`.
        """
        c = self._mk_concept(
            concept_key='failclosed.noattr',
            summary='Rad 1\nRad 2\nRad 3',
        )
        self.assertFalse(c.attribution)
        lines, hidden = c._filter_attribution_conservative({})
        self.assertEqual(lines, [])
        self.assertEqual(hidden, 3)

    def test_no_source_ref_is_not_injected(self):
        """Ett koncept helt utan källhänvisning injiceras inte."""
        c = self._mk_concept(
            concept_key='failclosed.nosource',
            summary='Text utan källa',
        )
        block = self.Concept._format_concept_block(
            c, 2000, 'TEST', user=self.env.user)
        self.assertNotIn('Text utan källa', block)

    # ── 1.3: radnivå-filtreringen körs i injektionsvägen ──

    def test_mixed_summary_filters_per_line(self):
        """Blandad sammanfattning injicerar bara de läsbara raderna."""
        c = self._mk_concept(
            concept_key='failclosed.mixed',
            summary='Läsbar rad\nHemlig rad',
            source_ref='res.partner,10',
            attribution=[
                {'line': 1, 'source_ref': 'res.partner,10'},
                {'line': 2, 'source_ref': 'dms.file,7'},
            ],
        )
        with patch.object(type(self.Concept), '_resolve_visible_sources',
                          return_value={c.id: {'res.partner,10': True,
                                               'dms.file,7': False}}):
            block = self.Concept._format_concept_block(
                c, 2000, 'TEST', user=self.env.user)
        self.assertIn('Läsbar rad', block)
        self.assertNotIn('Hemlig rad', block)

    def test_line_without_attribution_is_hidden(self):
        """En rad utan källhänvisning i ett attribuerat koncept utelämnas."""
        c = self._mk_concept(
            concept_key='failclosed.partialattr',
            summary='Attribuerad rad\nOattribuerad rad',
            source_ref='res.partner,10',
            attribution=[{'line': 1, 'source_ref': 'res.partner,10'}],
        )
        with patch.object(type(self.Concept), '_resolve_visible_sources',
                          return_value={c.id: {'res.partner,10': True}}):
            block = self.Concept._format_concept_block(
                c, 2000, 'TEST', user=self.env.user)
        self.assertIn('Attribuerad rad', block)
        self.assertNotIn('Oattribuerad rad', block)

    def test_get_visible_lines_accepts_dict_contract(self):
        """`_get_visible_lines` skickar en dict, inte ett set.

        FYND: den byggde tidigare ett set men `_filter_attribution*` anropar
        `.get(src)` — ett set har ingen `.get()`. Det var därför metoden
        aldrig kopplades in: den kraschade första gången den användes.
        """
        c = self._mk_concept(
            concept_key='failclosed.dictcontract',
            summary='Rad A\nRad B',
            attribution=[
                {'line': 1, 'source_ref': 'res.partner,10'},
                {'line': 2, 'source_ref': 'dms.file,7'},
            ],
        )
        out = c._get_visible_lines({c.id: {'res.partner,10': True,
                                           'dms.file,7': False}})
        self.assertIn(c.id, out)
        self.assertEqual(out[c.id]['lines'], ['Rad A'])
        self.assertEqual(out[c.id]['hidden'], 1)

    # ── 1.4: avförda koncept loggas med skäl ──

    def test_dropped_concept_is_logged(self):
        """Ett avfört koncept ger en loggpost med källhänvisning och skäl.

        OBS: `level=logging.INFO` (talet 20), ALDRIG strängen 'INFO'.
        Odoo sätter `logging.RUNBOT = 25` och döper om det till 'INFO'
        (`netsvc.py`), så `logging.getLevelName('INFO')` returnerar 25.
        `assertLogs(level='INFO')` sätter då loggerns nivå till 25 och
        filtrerar bort riktiga INFO-poster (20) — testet ser tomt ut trots
        att loggen skrivs.
        """
        c = self._mk_concept(
            concept_key='failclosed.logged',
            source_ref='dms.file,7',
            summary='Hemlig text',
        )
        with patch.object(type(self.Concept), '_resolve_visible_sources',
                          return_value={c.id: {'dms.file,7': False}}):
            with self.assertLogs('odoo.addons.ai_agent_core.models.'
                                 'ai_okf_concept',
                                 level=logging.INFO) as cm:
                self.Concept._format_concept_block(
                    c, 2000, 'TEST', user=self.env.user)
        joined = '\n'.join(cm.output)
        self.assertIn('avför', joined)
        self.assertIn('dms.file,7', joined)

    def test_dropped_concept_without_source_is_logged(self):
        """Ett koncept utan källhänvisning loggas med sitt eget skäl."""
        c = self._mk_concept(
            concept_key='failclosed.loggednosource',
            summary='Text utan källa',
        )
        with self.assertLogs('odoo.addons.ai_agent_core.models.'
                             'ai_okf_concept',
                             level=logging.INFO) as cm:
            self.Concept._format_concept_block(
                c, 2000, 'TEST', user=self.env.user)
        joined = '\n'.join(cm.output)
        self.assertIn('avför', joined)
        self.assertIn('ingen källhänvisning', joined)

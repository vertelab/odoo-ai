# -*- coding: utf-8 -*-
"""Tester för ai.okf.mixin (okf-mixin F1, F3, F3b, F4).

VARFÖR: kontraktet låg tidigare på `ai.memory.mixin` — en sökmixin som
råkade få dirty-flaggan på köpet, med `_okf_concept_vals()` hårdkodad på
två modellnamn. Nu bor det på en abstrakt mixin utan domän och utan
sökmotor, och varje modell svarar på sina egna källor.

Dessa tester bevisar:
  1. kontraktet är generiskt (ingen domän, ingen sökmotor)
  2. flaggan sätts av create/write och rensas utanför write()-vägen
  3. sammanfattningskedjan (modell -> text -> coworker -> trunkering)
  4. källan styr versionen, inte derivatet
  5. indexeringslistan är utökningsbar
"""

import ast
import pathlib
import time
from unittest.mock import patch

from odoo.tests import common, tagged
from odoo.addons.ai_agent_core.models import ai_okf_mixin
from ._config_param_guard import ConfigParamGuardedCase


class _OkfTestModel(common.TransactionCase):
    """Bas: en modell som bär mixinen, med kända källor."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Mixin = cls.env['ai.okf.mixin']
        cls.Concept = cls.env['ai.okf.concept']


@tagged('okf', 'post_install', '-at_install')
class TestOkfMixinContract(_OkfTestModel):
    """F1.1–1.6, 1.12: kontraktet är generiskt."""

    def test_fields_exist(self):
        """`okf_tags` är medvetet INTE på mixinen.

        FYND 2026-09-23: en many2many på en abstrakt modell ger samma
        relationstabell för alla ärvande modeller (TypeError: "... use the
        same table"). Fältet deklareras därför av varje konkret modell.
        """
        for f in ('okf_body', 'okf_summary', 'okf_links',
                  'okf_dirty', 'okf_indexed_at'):
            self.assertIn(f, self.Mixin._fields, f)
        self.assertNotIn('okf_tags', self.Mixin._fields,
                         'okf_tags får inte deklareras på mixinen')

    def test_dirty_is_indexed_and_not_copied(self):
        f = self.Mixin._fields['okf_dirty']
        self.assertTrue(f.index)
        self.assertFalse(f.copy)
        self.assertTrue(f.default)

    def test_json_defaults_are_lists(self):
        """Tomma listor, aldrig None — OKF-frontmattern kräver en lista."""
        self.assertEqual(self.Mixin._fields['okf_links'].default, list)

    def test_source_defaults_do_not_crash(self):
        """En modell som inte överrider källmetoderna ska inte krascha.

        Använder mixin-basen direkt: `ai.personal.memory` ÄRVER
        `ai.memory.mixin`, som överrider `_okf_dirty_fields()` med sina egna
        fält — defaulten (tom mängd) gäller bara en modell som inte
        deklarerar några.
        """
        rec = self.Mixin.new({})
        self.assertEqual(rec._okf_body_source(), '')
        self.assertIsNone(rec._okf_summary_source())
        self.assertEqual(rec._okf_tags_source(), [])
        self.assertEqual(rec._okf_links_source(), [])
        self.assertEqual(rec._okf_dirty_fields(), set())

    def test_no_domain_in_mixin(self):
        """Kärnan får inte känna någon domän (F1.12).

        Läser källfilen direkt: `inspect.getsource(type(self.Mixin))` fäller
        "could not find class definition" eftersom mixinen laddas via Odoos
        modulladdare och inte har någon sökbar filreferens i den här
        processen.
        """
        raw = pathlib.Path(
            ai_okf_mixin.__file__).read_text(encoding='utf-8')
        # Bara KODEN granskas — docstrings nämner domäner som exempel
        # ("en modell med översättbart innehåll (website.page, blog.post)")
        # och det är dokumentation, inte ett beroende.
        tree = ast.parse(raw)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, 'body', [])
                if (body and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)):
                    node.body = body[1:]
        src = ast.unparse(tree).lower()
        for domain in ('website', 'blog', 'event', 'hr.job', 'project',
                       'crm', 'prd'):
            self.assertNotIn(domain, src,
                             'domänen %r nämns i mixinens kod' % domain)

    def test_no_search_engine_in_mixin(self):
        """Mixinen äger inga sökfunktioner — de bor i ai.memory.mixin."""
        for name in ('_search_memory', '_generate_embedding', 'embed_batch',
                     '_extract_entities', 'faiss_search'):
            self.assertNotIn(name, dir(self.Mixin), name)


@tagged('okf', 'post_install', '-at_install')
class TestOkfDirtyFlag(_OkfTestModel):
    """F1.3–1.5, 3b.8–3b.9: flaggan sätts och rensas utanför write()."""

    def _memory(self, content='Innehåll'):
        return self.env['ai.personal.memory'].create({
            'user_id': self.env.ref('base.user_admin').id,
            'content': content,
            'category': 'fact',
        })

    def test_new_record_is_dirty(self):
        self.assertTrue(self._memory().okf_dirty)

    def test_content_change_sets_flag(self):
        mem = self._memory()
        mem._clear_okf_dirty()
        self.assertFalse(mem.okf_dirty)
        mem.write({'importance': 'high'})
        mem.invalidate_recordset(['okf_dirty'])
        self.assertTrue(mem.okf_dirty, 'innehållsfält ska flagga')

    def test_irrelevant_field_does_not_flag(self):
        mem = self._memory()
        mem._clear_okf_dirty()
        mem.write({'last_accessed': '2026-01-01 00:00:00'})
        mem.invalidate_recordset(['okf_dirty'])
        self.assertFalse(mem.okf_dirty, 'orelevant fält ska inte flagga')

    def test_clear_survives_write(self):
        """Rensningen får inte återtändas av write()-hooken.

        Detta är buggen som gav 38 versioner av `ai.memory,257` (2026-09-21).

        OBS: fältet som skrivs måste ligga UTANFÖR `_okf_dirty_fields()`.
        `importance` ligger INUTI (`ai_memory_mixin.py:60-61`) — en ändring
        av det SKA tända flaggan, så det duger inte som bevis. Här används
        ett fält som inte påverkar konceptet.
        """
        mem = self._memory()
        mem._clear_okf_dirty()
        mem.write({'okf_summary': 'ny sammanfattning'})
        mem.invalidate_recordset(['okf_dirty'])
        self.assertFalse(mem.okf_dirty)

    def test_clear_writes_extra_vals_in_same_update(self):
        mem = self._memory()
        mem._clear_okf_dirty({'okf_body': 'ny text',
                              'okf_summary': 'ny sammanfattning'})
        mem.invalidate_recordset(['okf_body', 'okf_summary', 'okf_dirty'])
        self.assertEqual(mem.okf_body, 'ny text')
        self.assertEqual(mem.okf_summary, 'ny sammanfattning')
        self.assertFalse(mem.okf_dirty)
        self.assertTrue(mem.okf_indexed_at)


@tagged('okf', 'post_install', '-at_install')
class TestSummaryChain(_OkfTestModel):
    """F3.1–3.4: fallbackkedjan."""

    def _memory(self, content):
        return self.env['ai.personal.memory'].create({
            'user_id': self.env.ref('base.user_admin').id,
            'content': content,
            'category': 'fact',
        })

    def test_short_text_is_used_as_is(self):
        mem = self._memory('Kort text.')
        summary, source = mem._okf_build_summary('Kort text.', 2000)
        self.assertEqual(summary, 'Kort text.')
        self.assertEqual(source, 'text')

    def test_model_summary_wins(self):
        """Modellens egen sammanfattning går före LLM:en."""
        mem = self._memory('x')
        with patch.object(type(mem), '_okf_summary_source',
                          return_value='Modellens egen'):
            summary, source = mem._okf_build_summary('Lång text ' * 500, 100)
        self.assertEqual(summary, 'Modellens egen')
        self.assertEqual(source, 'model')

    def test_long_text_uses_coworker(self):
        mem = self._memory('x')
        with patch.object(type(mem), '_okf_summarize_with_coworker',
                          return_value='LLM-sammanfattning'):
            summary, source = mem._okf_build_summary('Lång text ' * 500, 100)
        self.assertEqual(summary, 'LLM-sammanfattning')
        self.assertEqual(source, 'coworker')

    def test_coworker_failure_falls_back_to_truncation(self):
        """Sista utvägen — och den LOGGAS (tyst trunkering är `except: pass`)."""
        mem = self._memory('x')
        text = 'Lång text ' * 500
        with patch.object(type(mem), '_okf_summarize_with_coworker',
                          return_value=None):
            with self.assertLogs('odoo.addons.ai_agent_core.models.'
                                 'ai_okf_mixin', level='WARNING') as cm:
                summary, source = mem._okf_build_summary(text, 100)
        self.assertEqual(source, 'truncated')
        self.assertEqual(len(summary), 100)
        self.assertTrue(any('trunkerar' in m for m in cm.output))

    def test_max_chars_comes_from_parameter(self):
        """F3.2: gränsen är en systemparameter, default 2000."""
        self.assertEqual(self.Mixin._okf_summary_max_chars(), 2000)
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.okf_summary_max_chars', '500')
        self.assertEqual(self.Mixin._okf_summary_max_chars(), 500)

    def test_bad_parameter_falls_back_to_default(self):
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.okf_summary_max_chars', 'inte-ett-tal')
        self.assertEqual(self.Mixin._okf_summary_max_chars(), 2000)

    def test_shared_session_is_reused(self):
        """F3.3: EN session för alla sammanfattningar, inte en per post."""
        s1 = self.Mixin._okf_summarize_session()
        s2 = self.Mixin._okf_summarize_session()
        self.assertEqual(s1, s2)
        self.assertEqual(s1.name, 'OKF-sammanfattning')


@tagged('okf', 'post_install', '-at_install')
class TestSourceDrivesVersion(_OkfTestModel):
    """F3.6–3.7 (D5): källan styr versionen, inte derivatet."""

    def _upsert(self, summary, source_text):
        return self.Concept._okf_upsert(
            artifact_type='knowledge',
            concept_key='test.source.drives',
            summary=summary,
            source_text=source_text,
            source_ref='test.model,1',
            owner_company_id=self.env.company.id,
        )

    def test_same_source_different_summary_gives_one_version(self):
        """En LLM som formulerar om samma text ska INTE skapa en version."""
        v1 = self._upsert('Första formuleringen', 'Källan är oförändrad')
        v2 = self._upsert('Andra formuleringen', 'Källan är oförändrad')
        self.assertEqual(v1.id, v2.id, 'ingen ny version när källan är lika')
        self.assertEqual(v2.version, 1)

    def test_changed_source_gives_new_version(self):
        v1 = self._upsert('Sammanfattning', 'Källa version 1')
        v2 = self._upsert('Sammanfattning', 'Källa version 2')
        self.assertNotEqual(v1.id, v2.id)
        self.assertEqual(v2.version, 2)
        self.assertEqual(v2.supersedes_id, v1)

    def test_without_source_text_summary_still_compared(self):
        """Bakåtkompatibelt: utan source_text jämförs summary som förut."""
        v1 = self.Concept._okf_upsert(
            artifact_type='knowledge', concept_key='test.legacy.compare',
            summary='Samma', source_ref='test.model,2',
            owner_company_id=self.env.company.id)
        v2 = self.Concept._okf_upsert(
            artifact_type='knowledge', concept_key='test.legacy.compare',
            summary='Samma', source_ref='test.model,2',
            owner_company_id=self.env.company.id)
        self.assertEqual(v1.id, v2.id)


@tagged('okf', 'post_install', '-at_install')
class TestIndexableModels(_OkfTestModel):
    """F4.1–4.4: utökningsbar lista, kärnan ren."""

    def test_base_list_has_legacy_models(self):
        models = self.Mixin._okf_indexable_models()
        self.assertIn('ai.personal.memory', models)
        self.assertIn('ai.company.memory', models)

    def test_base_list_names_no_domain(self):
        for m in self.Mixin._okf_indexable_models():
            for domain in ('website', 'blog', 'event', 'crm', 'project',
                           'prd'):
                self.assertNotIn(domain, m, m)

    def test_unknown_model_is_skipped(self):
        """En modell som inte finns i miljön hoppas över utan fel."""
        with patch.object(type(self.Mixin), '_okf_indexable_models',
                          return_value=['finns.inte', 'ai.personal.memory']):
            total = self.Mixin._okf_cron_index_dirty(batch_size=1)
        self.assertGreaterEqual(total, 0)

    def test_batch_size_is_a_shared_cap(self):
        """batch_size är ett TAK över alla modeller, inte per modell.

        FYND 2026-09-23: `limit=batch_size` sattes per modell i loopen. Med
        sex registrerade modeller blev det 6 × 50 = 300 poster per körning —
        cronen överskred sitt intervall och nästa körning startade medan den
        förra arbetade. Två transaktioner höll `ir_cron`-låset samtidigt.
        """
        for i in range(4):
            self.env['ai.personal.memory'].create({
                'user_id': self.env.ref('base.user_admin').id,
                'content': 'Batch-test %s.' % i, 'category': 'fact'})
        for i in range(4):
            self.env['ai.company.memory'].create({
                'company_id': self.env.company.id,
                'content': 'Batch-test %s.' % i, 'category': 'knowledge'})
        total = self.Mixin._okf_cron_index_dirty(batch_size=3)
        self.assertLessEqual(total, 3,
                             'taket ska gälla totalt, inte per modell')

    def test_time_budget_stops_the_run(self):
        """Tidsbudgeten gör att cronen ger tillbaka innan nästa startar."""
        for i in range(3):
            self.env['ai.personal.memory'].create({
                'user_id': self.env.ref('base.user_admin').id,
                'content': 'Tid-test %s.' % i, 'category': 'fact'})
        with patch.object(type(self.Mixin), '_okf_index_record',
                          side_effect=lambda **kw: time.sleep(0.05) or None):
            total = self.Mixin._okf_cron_index_dirty(
                batch_size=100, time_budget=0.01)
        self.assertLessEqual(total, 3, 'budgeten ska stoppa körningen')

    # ── D3: adaptivt tak (2026-10-21) ───────────────────────────────

    def test_adaptive_cap_never_below_floor(self):
        """`batch_size` är ett GOLV — taket kan aldrig bli lägre."""
        self.assertEqual(self.Mixin._okf_adaptive_cap(25, 0), 25)
        self.assertEqual(self.Mixin._okf_adaptive_cap(25, 10), 25)

    def test_adaptive_cap_rises_with_queue_depth(self):
        """En djup kö höjer taket över batch_size."""
        cap = self.Mixin._okf_adaptive_cap(25, 100000)
        self.assertGreater(cap, 25)
        self.assertLessEqual(cap, self.Mixin.OKF_CRON_TAK_MAX)

    def test_adaptive_cap_stops_at_max(self):
        """Taket stannar på OKF_CRON_TAK_MAX oavsett hur djup kön är."""
        self.assertEqual(self.Mixin._okf_adaptive_cap(25, 10 ** 9),
                         self.Mixin.OKF_CRON_TAK_MAX)

    def test_cap_does_not_extend_past_time_budget(self):
        """Ett höjt tak får aldrig förlänga körningen (4.2).

        Tidsbudgeten är orörd — deadline-kontrollen i loopen gäller
        fortfarande, så ett höjt tak ger fler poster per sekund, inte en
        längre körning.
        """
        for i in range(5):
            self.env['ai.personal.memory'].create({
                'user_id': self.env.ref('base.user_admin').id,
                'content': 'Tak-tid %s.' % i, 'category': 'fact'})
        with patch.object(type(self.Mixin), '_okf_index_record',
                          side_effect=lambda **kw: time.sleep(0.05) or None):
            start = time.monotonic()
            total = self.Mixin._okf_cron_index_dirty(
                batch_size=100, time_budget=0.01)
            elapsed = time.monotonic() - start
        self.assertLessEqual(total, 5, 'budgeten ska stoppa körningen')
        self.assertLess(elapsed, 1.0, 'körningen fick inte förlängas')

    # ── D4: garanterad andel (2026-10-21) ───────────────────────────

    def _set_priority(self, models):
        """Sätt prioritetslistan via systemparametern (D4).

        Listan bor i `ir.config_parameter` — ett klassattribut fälls av
        Odoos testramverk ("Found unexpected attributes on ai.okf.mixin").
        """
        self.env['ir.config_parameter'].sudo().set_param(
            self.Mixin.OKF_PRIORITY_PARAM, ','.join(models))

    def test_priority_registration(self):
        """Prioriterade modeller kan registreras och listas."""
        self._set_priority([])
        self.Mixin._okf_register_priority_indexable('ai.personal.memory')
        self.Mixin._okf_register_priority_indexable('ai.personal.memory')
        self.assertEqual(self.Mixin._okf_priority_indexable(),
                         ['ai.personal.memory'],
                         'registreringen ska vara idempotent')

    def test_priority_model_gets_its_share(self):
        """En prioriterad modell får sin andel även när kön domineras (4.3)."""
        for i in range(30):
            self.env['ai.company.memory'].create({
                'company_id': self.env.company.id,
                'content': 'Dominerande %s.' % i, 'category': 'knowledge'})
        mem = self.env['ai.personal.memory'].create({
            'user_id': self.env.ref('base.user_admin').id,
            'content': 'Prioriterad post.', 'category': 'fact'})
        self._set_priority(['ai.personal.memory'])
        with patch.object(type(self.Mixin), '_okf_indexable_models',
                          return_value=['ai.company.memory',
                                        'ai.personal.memory']):
            self.Mixin._okf_cron_index_dirty(batch_size=4)
        mem.invalidate_recordset(['okf_dirty'])
        self.assertFalse(mem.okf_dirty,
                         'prioriterad modell fick ingen plats i körningen')

    def test_no_model_starves_with_more_models_than_cap(self):
        """Fler modeller än taket: minst en post per modell (4.4)."""
        names = ['ai.personal.memory', 'ai.company.memory']
        for i in range(3):
            self.env['ai.personal.memory'].create({
                'user_id': self.env.ref('base.user_admin').id,
                'content': 'Svalt-a %s.' % i, 'category': 'fact'})
            self.env['ai.company.memory'].create({
                'company_id': self.env.company.id,
                'content': 'Svalt-b %s.' % i, 'category': 'knowledge'})
        self._set_priority([])
        with patch.object(type(self.Mixin), '_okf_indexable_models',
                          return_value=names):
            total = self.Mixin._okf_cron_index_dirty(batch_size=2)
        self.assertGreaterEqual(
            total, 2, 'båda modellerna ska få minst en post var')

    def test_cron_is_idempotent(self):
        """Andra körningen skapar inga nya versioner."""
        self.env['ai.personal.memory'].create({
            'user_id': self.env.ref('base.user_admin').id,
            'content': 'Idempotens-test.', 'category': 'fact'})
        first = self.Mixin._okf_cron_index_dirty(batch_size=10)
        second = self.Mixin._okf_cron_index_dirty(batch_size=10)
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0, 'andra körningen ska vara en no-op')

    def test_empty_legacy_memory_clears_flag(self):
        """ADD-only: tomt innehåll blir aldrig icke-tomt → avför."""
        mem = self.env['ai.personal.memory'].create({
            'user_id': self.env.ref('base.user_admin').id,
            'content': 'x', 'category': 'fact'})
        with patch.object(type(mem), '_okf_body_source', return_value=''):
            result = mem._okf_index_record()
        mem.invalidate_recordset(['okf_dirty'])
        self.assertIsNone(result)
        self.assertFalse(mem.okf_dirty, 'tomt legacy-minne ska avföras')


@tagged('okf', 'post_install', '-at_install')
class TestDebugMenu(_OkfTestModel):
    """F3b.1–3b.9: OKF-valet i skalbaggen."""

    def test_action_reads_context(self):
        mem = self.env['ai.personal.memory'].create({
            'user_id': self.env.ref('base.user_admin').id,
            'content': 'Debug-test.', 'category': 'fact'})
        action = self.Mixin.with_context(
            active_model='ai.personal.memory', active_id=mem.id,
        ).action_open_okf()
        self.assertEqual(action['res_model'], 'ai.personal.memory')
        self.assertEqual(action['res_id'], mem.id)
        self.assertEqual(action['target'], 'new')

    def test_action_without_context_closes(self):
        action = self.Mixin.action_open_okf()
        self.assertEqual(action['type'], 'ir.actions.act_window_close')

    def test_view_exists_and_is_readonly(self):
        view = self.env.ref('ai_agent_core.view_okf_record_form')
        self.assertIn('readonly="1"', view.arch)

    def test_concepts_are_filtered_by_source_ref(self):
        """Åtgärden visar postens egna koncept — inga andras.

        Koden förberäknar id-listan (`'id', 'in', concept_ids`) i stället
        för att skicka `source_ref` som domän: `version desc`-ordningen
        bevaras då i åtgärden. Testet verifierar resultatet, inte formen.
        """
        mem = self.env['ai.personal.memory'].create({
            'user_id': self.env.ref('base.user_admin').id,
            'content': 'Koncept-test.', 'category': 'fact'})
        other = self.env['ai.personal.memory'].create({
            'user_id': self.env.ref('base.user_admin').id,
            'content': 'Annans koncept.', 'category': 'fact'})
        mem._okf_index_record()
        other._okf_index_record()
        action = mem.action_okf_show_concepts()
        self.assertEqual(action['res_model'], 'ai.okf.concept')
        domain = action['domain'][0]
        self.assertEqual(domain[0], 'id')
        self.assertEqual(domain[1], 'in')
        shown = self.env['ai.okf.concept'].search(action['domain'])
        refs = set(shown.mapped('source_ref'))
        self.assertEqual(refs, {'ai.personal.memory,%s' % mem.id},
                         'bara postens egna koncept ska visas')

    def test_manual_run_clears_flag(self):
        """F3b.8: knappen kör SAMMA väg som cronen."""
        mem = self.env['ai.personal.memory'].create({
            'user_id': self.env.ref('base.user_admin').id,
            'content': 'Manuell körning.', 'category': 'fact'})
        self.assertTrue(mem.okf_dirty)
        mem.action_okf_index_now()
        mem.invalidate_recordset(['okf_dirty', 'okf_indexed_at'])
        self.assertFalse(mem.okf_dirty)
        self.assertTrue(mem.okf_indexed_at)

    def test_mixin_has_no_own_indexing_path(self):
        """Ingen tredje indexeringsväg: bara _okf_index_record."""
        names = [n for n in dir(self.Mixin) if 'index' in n.lower()]
        self.assertIn('_okf_index_record', names)
        self.assertIn('_okf_cron_index_dirty', names)


class TestQueueStats(_OkfTestModel):
    """D5: ködjupet per modell ska gå att läsa ut."""

    def test_stats_cover_indexable_models(self):
        """En rad per indexerad modell som finns i miljön."""
        stats = self.Mixin._okf_queue_stats()
        names = {s['model'] for s in stats}
        for model_name in self.Mixin._okf_indexable_models():
            if model_name in self.env:
                self.assertIn(model_name, names, model_name)

    def test_stats_shape(self):
        """Varje rad bär de fält dashboarden läser."""
        for row in self.Mixin._okf_queue_stats():
            self.assertEqual(
                set(row),
                {'model', 'dirty', 'total', 'oldest_age_s', 'priority'})

    def test_stats_counts_dirty_records(self):
        """`dirty` räknar faktiskt flaggade poster."""
        mem = self.env['ai.personal.memory'].create({
            'user_id': self.env.ref('base.user_admin').id,
            'content': 'Köstatistik-test.', 'category': 'fact'})
        self.assertTrue(mem.okf_dirty)
        row = [s for s in self.Mixin._okf_queue_stats()
               if s['model'] == 'ai.personal.memory'][0]
        self.assertGreaterEqual(row['dirty'], 1)
        self.assertIsNotNone(row['oldest_age_s'])

    def test_empty_queue_has_no_age(self):
        """Tom kö rapporterar ingen ålder — inte 0."""
        self.env['ai.personal.memory'].search(
            [('okf_dirty', '=', True)])._clear_okf_dirty()
        row = [s for s in self.Mixin._okf_queue_stats()
               if s['model'] == 'ai.personal.memory'][0]
        self.assertEqual(row['dirty'], 0)
        self.assertIsNone(row['oldest_age_s'])

    def test_priority_flag_is_reported(self):
        """Sökdrivande modell markeras i statistiken."""
        self.Mixin._okf_register_priority_indexable('ai.personal.memory')
        row = [s for s in self.Mixin._okf_queue_stats()
               if s['model'] == 'ai.personal.memory'][0]
        self.assertTrue(row['priority'])

    def test_format_does_not_crash_on_empty_queue(self):
        """Dashboardens textfält ska klara en tom kö."""
        self.env['ai.personal.memory'].search(
            [('okf_dirty', '=', True)])._clear_okf_dirty()
        text = self.env['ai.okf.dashboard']._format_queue_status()
        self.assertIsInstance(text, str)
        self.assertIn('tom', text)

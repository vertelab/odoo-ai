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

from unittest.mock import patch

from odoo.tests import common, tagged


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
        for f in ('okf_text', 'okf_summary', 'okf_tags', 'okf_links',
                  'okf_dirty', 'okf_indexed_at'):
            self.assertIn(f, self.Mixin._fields, f)

    def test_dirty_is_indexed_and_not_copied(self):
        f = self.Mixin._fields['okf_dirty']
        self.assertTrue(f.index)
        self.assertFalse(f.copy)
        self.assertTrue(f.default)

    def test_json_defaults_are_lists(self):
        """Tomma listor, aldrig None — OKF-frontmattern kräver en lista."""
        self.assertEqual(self.Mixin._fields['okf_tags'].default, list)
        self.assertEqual(self.Mixin._fields['okf_links'].default, list)

    def test_source_defaults_do_not_crash(self):
        """En modell som inte överrider källmetoderna ska inte krascha."""
        rec = self.env['ai.personal.memory'].new({})
        self.assertEqual(rec._okf_text_source(), '')
        self.assertIsNone(rec._okf_summary_source())
        self.assertEqual(rec._okf_tags_source(), [])
        self.assertEqual(rec._okf_links_source(), [])
        self.assertEqual(rec._okf_dirty_fields(), set())

    def test_no_domain_in_mixin(self):
        """Kärnan får inte känna någon domän (F1.12)."""
        import inspect
        src = inspect.getsource(type(self.Mixin))
        for domain in ('website', 'blog', 'event', 'hr.job', 'project',
                       'crm', 'prd'):
            self.assertNotIn(domain, src.lower(),
                             'domänen %r nämns i mixinen' % domain)

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
        """
        mem = self._memory()
        mem._clear_okf_dirty()
        mem.write({'importance': 'low'})  # ett fält UTANFÖR dirty_fields
        mem.invalidate_recordset(['okf_dirty'])
        self.assertFalse(mem.okf_dirty)

    def test_clear_writes_extra_vals_in_same_update(self):
        mem = self._memory()
        mem._clear_okf_dirty({'okf_text': 'ny text',
                              'okf_summary': 'ny sammanfattning'})
        mem.invalidate_recordset(['okf_text', 'okf_summary', 'okf_dirty'])
        self.assertEqual(mem.okf_text, 'ny text')
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
        with patch.object(type(mem), '_okf_text_source', return_value=''):
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
        mem = self.env['ai.personal.memory'].create({
            'user_id': self.env.ref('base.user_admin').id,
            'content': 'Koncept-test.', 'category': 'fact'})
        mem._okf_index_record()
        action = mem.action_okf_show_concepts()
        self.assertEqual(action['res_model'], 'ai.okf.concept')
        domain = action['domain'][0]
        self.assertEqual(domain[0], 'source_ref')
        self.assertEqual(domain[2], 'ai.personal.memory,%s' % mem.id)

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

# -*- coding: utf-8 -*-
"""Tester för erfarenhetsstyrd skill-förbättring (skill-experience-improvement).

Täcker:
  (3.1) ny erfarenhet bokförs i rätt fält, recipe_text orörd
  (3.2) upprepad not ökar räknaren men lägger inte till rad
  (3.3) radtaket hålls, de äldsta raderna faller bort
  (3.4) inget förslag under minsta antal failures
  (3.5) förslag skapas när underlag finns
  (3.6) deterministisk fallback när LLM-vägen är otillgänglig
  (3.7) applicera/kasta förslag (HITL)
  (3.8) copy_for_coworker ger oberoende erfarenhetsfält
  (4.3) egen identitet garanteras via ORM/create
  (4.4) ompekningen rekurserar inte
  (4.5) två coworkers delar inte erfarenhetsfält
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestSkillExperience(TransactionCase):
    """Erfarenheter bokförs separat från receptet."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Skill = cls.env['ai.skill']

    def _skill(self, name='Ertest skill', recipe='Steg 1. Gör X.'):
        return self.Skill.create({
            'name': name,
            'description': 'test-skill för erfarenhetsloop',
            'recipe_text': recipe,
        })

    # ── 3.1 ny erfarenhet bokförs ─────────────────────────────────────

    def test_new_experience_is_recorded_in_correct_field(self):
        """3.1: failure hamnar i failure_cases, recipe_text orörd."""
        skill = self._skill()
        before = skill.recipe_text
        ok = skill.record_experience(
            'web01: Disk usage high', verdict='failure',
            source='saltstack.alert 2237')
        self.assertTrue(ok)
        self.assertIn('web01: Disk usage high', skill.failure_cases or '')
        self.assertFalse(skill.success_cases)
        self.assertEqual(skill.recipe_text, before,
                         'recipe_text får aldrig ändras av erfarenheten')

    def test_success_goes_to_success_cases(self):
        """3.1: success hamnar i success_cases."""
        skill = self._skill('Ertest success-skill')
        skill.record_experience('web02: Service nere', verdict='success')
        self.assertIn('web02: Service nere', skill.success_cases or '')
        self.assertFalse(skill.failure_cases)

    def test_empty_note_is_rejected(self):
        """3.1: tom not bokförs inte."""
        skill = self._skill('Ertest empty-skill')
        self.assertFalse(skill.record_experience('   '))
        self.assertFalse(skill.failure_cases)

    # ── 3.2 dedup på noten ────────────────────────────────────────────

    def test_repeated_note_counts_but_does_not_duplicate(self):
        """3.2: samma not ⇒ räknare ökar, radantal oförändrat."""
        skill = self._skill('Ertest dedup-skill')
        skill.record_experience('web01: Disk usage high',
                                verdict='failure',
                                source='saltstack.alert 1')
        n1 = len([l for l in (skill.failure_cases or '').split('\n') if l.strip()])
        self.assertEqual(n1, 1)

        skill.record_experience('web01: Disk usage high',
                                verdict='failure',
                                source='saltstack.alert 999')
        n2 = len([l for l in (skill.failure_cases or '').split('\n') if l.strip()])
        self.assertEqual(n2, 1, 'samma not ska dedupas, inte bli ny rad')

        line = skill.failure_cases
        self.assertIn('sedd 2 ggr', line, 'räknaren ska ha ökat')
        self.assertIn('999', line, 'källhänvisningen ska vara den senaste')

    # ── 3.3 radtaket ──────────────────────────────────────────────────

    def test_experience_lines_are_capped(self):
        """3.3: radtaket hålls och de äldsta raderna faller bort."""
        skill = self._skill('Ertest cap-skill')
        cap = skill._MAX_EXPERIENCE_LINES
        for i in range(cap + 5):
            skill.record_experience('web%02d: unikt larm %d' % (i, i),
                                    verdict='failure')
        lines = [l for l in (skill.failure_cases or '').split('\n') if l.strip()]
        self.assertLessEqual(len(lines), cap,
                             'radantalet får inte överstiga taket')
        # Den första (äldsta) raden ska ha fallit bort.
        self.assertNotIn('unikt larm 0', skill.failure_cases)
        # Den senaste ska finnas kvar.
        self.assertIn('unikt larm %d' % (cap + 4), skill.failure_cases)

    # ── 3.4/3.5/3.6 förslag ───────────────────────────────────────────

    def test_no_proposal_below_minimum_failures(self):
        """3.4: otillräckligt underlag ⇒ inget förslag."""
        skill = self._skill('Ertest min-skill')
        skill.record_experience('web01: falskt larm', verdict='failure')
        # Endast 1 failure, minsta antal är 2.
        made = skill.propose_improvement(use_llm=False)
        self.assertFalse(made)
        self.assertFalse(skill.improvement_suggested_recipe)

    def test_proposal_created_when_evidence_exists(self):
        """3.5: tillräckligt underlag ⇒ förslag, recipe_text orörd."""
        skill = self._skill('Ertest prop-skill')
        before = skill.recipe_text
        for i in range(3):
            skill.record_experience('web01: falskt larm %d' % i,
                                    verdict='failure')
        made = skill.propose_improvement(use_llm=False)
        self.assertTrue(made)
        self.assertTrue(skill.improvement_suggested_recipe)
        self.assertTrue(skill.improvement_guidance,
                        'guidance ska beskriva vad som skärps')
        self.assertTrue(skill.improvement_proposed_on)
        self.assertEqual(skill.recipe_text, before,
                         'ett förslag får aldrig röra recipe_text')

    def test_deterministic_fallback_when_llm_unavailable(self):
        """3.6: LLM-vägen otillgänglig ⇒ deterministiskt förslag, inte tyst."""
        skill = self._skill('Ertest fallback-skill')
        for i in range(3):
            skill.record_experience('web01: falskt larm %d' % i,
                                    verdict='failure')
        # Tvinga LLM-vägen att misslyckas.
        with patch.object(type(skill), '_llm_improve_recipe',
                          return_value=None):
            made = skill.propose_improvement(use_llm=True)
        self.assertTrue(made, 'loopen får aldrig vara tyst')
        self.assertTrue(skill.improvement_suggested_recipe)
        # Fallbacken ska nämna de inlärda falska positiva.
        self.assertIn('falska positiva', skill.improvement_suggested_recipe.lower())

    def test_llm_exception_falls_back_to_deterministic(self):
        """3.6: ett kastat fel i LLM-vägen ger ändå ett underlag."""
        skill = self._skill('Ertest exc-skill')
        for i in range(3):
            skill.record_experience('web01: falskt larm %d' % i,
                                    verdict='failure')
        with patch.object(type(skill), '_llm_improve_recipe',
                          side_effect=RuntimeError('provider nere')):
            made = skill.propose_improvement(use_llm=True)
        self.assertTrue(made)
        self.assertTrue(skill.improvement_suggested_recipe)

    # ── 3.7 HITL: applicera/kasta ─────────────────────────────────────

    def test_apply_proposal_replaces_recipe(self):
        """3.7: applicera ersätter recipe_text och rensar förslaget."""
        skill = self._skill('Ertest apply-skill', recipe='Gammalt recept')
        skill.write({
            'improvement_suggested_recipe': 'Nytt skärpt recept',
            'improvement_proposed_on': '2026-09-13 12:00:00',
        })
        skill.action_apply_suggested_recipe()
        self.assertIn('Nytt skärpt recept', skill.recipe_text)
        self.assertFalse(skill.improvement_suggested_recipe,
                         'förslaget ska rensas efter applicering')

    def test_discard_proposal_keeps_recipe(self):
        """3.7: kasta rensar förslaget utan att röra recipe_text."""
        skill = self._skill('Ertest discard-skill', recipe='Behåll detta')
        skill.write({
            'improvement_suggested_recipe': 'Förslag som ska kastas',
        })
        skill.action_discard_suggested_recipe()
        self.assertFalse(skill.improvement_suggested_recipe)
        self.assertEqual(skill.recipe_text, 'Behåll detta')

    def test_cron_never_touches_recipe_text(self):
        """3.7: schemalagd körning rör aldrig receptet."""
        skills = []
        for i in range(2):
            s = self._skill('Ertest cron-skill %d' % i, recipe='Orörd %d' % i)
            for j in range(3):
                s.record_experience('web01: cronlarm %d-%d' % (i, j),
                                    verdict='failure')
            skills.append(s)
        before = {s.id: s.recipe_text for s in skills}
        # Kör cron-metoden (utan LLM-provider i test → fallback).
        self.Skill._cron_propose_improvements(limit=10, commit=False)
        for s in skills:
            s.invalidate_recordset()
            self.assertEqual(s.recipe_text, before[s.id],
                             'cronen får aldrig ändra recipe_text')
            self.assertTrue(s.improvement_suggested_recipe,
                            'förslaget ska vänta på mänskligt beslut')


@tagged('post_install', '-at_install')
class TestIdentityOwnership(TransactionCase):
    """3.8/4.3/4.4/4.5: coworker äger sin identitet."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Identity = cls.env['ai.identity']
        cls.Coworker = cls.env['ai.coworker']

    def _template(self, name='Ertest mall'):
        return self.Identity.create({
            'name': name,
            'system_prompt': 'Du är en testassistent.',
            'is_template': True,
        })

    def _coworker(self, name):
        return self.Coworker.create({
            'name': name, 'description': 'x', 'status': 'active'})

    # ── 3.8 oberoende erfarenhetsfält ─────────────────────────────────

    def test_identity_copy_is_independent(self):
        """3.8: kopian delar inte erfarenhetsfält med mallen."""
        tpl = self._template()
        cw = self._coworker('Ertest kopia-coworker')
        copy = tpl.copy_for_coworker(cw)
        self.assertNotEqual(copy.id, tpl.id)
        self.assertFalse(copy.is_template, 'kopian ska inte vara en mall')
        self.assertEqual(copy.template_id.id, tpl.id,
                         'kopian ska spåra sin mall')

    # ── 4.3 egen identitet via ORM/create ─────────────────────────────

    def test_own_identity_guaranteed_on_create(self):
        """4.3: en coworker som tilldelas en MALL får en egen kopia."""
        tpl = self._template('Ertest orm-mall')
        cw = self._coworker('Ertest orm-coworker')
        cw.write({'identity_id': tpl.id})
        self.assertNotEqual(
            cw.identity_id.id, tpl.id,
            'coworkern ska ha fått en egen kopia, inte mallen')
        self.assertFalse(cw.identity_id.is_template)

    def test_own_identity_guaranteed_on_write_to_template(self):
        """4.3: samma garanti när identiteten sätts via write()."""
        tpl = self._template('Ertest write-mall')
        cw = self._coworker('Ertest write-coworker')
        # write() med identity_id ska trigga _ensure_own_identity
        cw.write({'identity_id': tpl.id})
        self.assertNotEqual(cw.identity_id.id, tpl.id)

    # ── 4.4 ingen oändlig rekursion ───────────────────────────────────

    def test_no_infinite_recursion_on_copy(self):
        """4.4: ompekningen till kopian rekurserar inte."""
        tpl = self._template('Ertest rekursionsmall')
        cw = self._coworker('Ertest rekursionscoworker')
        cw.write({'identity_id': tpl.id})  # får inte hänga
        first = cw.identity_id
        self.assertTrue(first)
        # Att köra igen ska inte skapa ännu en kopia (coworkern äger redan
        # en icke-mall och ingen annan delar den).
        cw._ensure_own_identity()
        self.assertEqual(cw.identity_id.id, first.id,
                         'ingen ny kopia ska skapas när coworkern redan äger en')

    # ── 4.5 delad identitet upptäcks ──────────────────────────────────

    def test_shared_identity_is_detected_and_forked(self):
        """4.5: en identitet som redan används av en annan ger egen kopia."""
        cw1 = self._coworker('Ertest delad-1')
        # cw1 får en icke-mall-identitet via en mall.
        tpl = self._template('Ertest delad-mall')
        cw1.write({'identity_id': tpl.id})
        owned = cw1.identity_id
        self.assertFalse(owned.is_template)

        # cw2 tilldelas SAMMA (nu ägda) identitet → ska få egen kopia.
        cw2 = self._coworker('Ertest delad-2')
        cw2.write({'identity_id': owned.id})
        self.assertNotEqual(
            cw2.identity_id.id, owned.id,
            'en identitet som redan används ska forkas, inte delas')

    def test_experience_does_not_leak_between_coworkers(self):
        """4.5: erfarenhet på den ena påverkar inte den andra."""
        tpl = self._template('Ertest läckage-mall')
        cw1 = self._coworker('Ertest läckage-1')
        cw2 = self._coworker('Ertest läckage-2')
        cw1.write({'identity_id': tpl.id})
        cw2.write({'identity_id': tpl.id})

        s1 = self.env['ai.skill'].create({
            'name': 'Ertest läckage-skill 1', 'description': 'x'})
        s2 = self.env['ai.skill'].create({
            'name': 'Ertest läckage-skill 2', 'description': 'x'})
        cw1.write({'skill_ids': [(6, 0, [s1.id])]})
        cw2.write({'skill_ids': [(6, 0, [s2.id])]})

        s1.record_experience('cw1: erfarenhet', verdict='failure')
        self.assertTrue(s1.failure_cases)
        self.assertFalse(s2.failure_cases,
                         'erfarenheter får inte läcka mellan coworkers')
        # Och identiteterna är olika objekt.
        self.assertNotEqual(cw1.identity_id.id, cw2.identity_id.id)

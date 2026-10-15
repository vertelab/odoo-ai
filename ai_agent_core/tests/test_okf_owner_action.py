# -*- coding: utf-8 -*-
"""Ägar-åtgärden `_okf_action_for_owner()` (okf-smart-buttons F2).

VARFÖR: smartknapparna på `res.users` (Min profil), `res.company` och
`ai.coworker` ställer samma fråga — *vilka koncept ÄGER den här posten?* —
och svarade tidigare var för sig. Frågan bor nu i kärnan på
`ai.okf.concept`, med `OKF_OWNER_MODELS` som enda karta från modell till
ägarfält.

Skillnaden mot `action_okf_show_concepts()` (som följer `source_ref`) är
avsiktlig: ägande och ursprung är två olika frågor. Ett koncept kan ägas
av en användare utan att användarposten var källan.

Dessa tester bevisar:
  1. tre ägarmodeller ger tre OLIKA domäner (rätt fält, rätt id)
  2. okänd modell ger `False` — ingen krasch
  3. `create=False` i kontexten (koncept är ADD-only)
  4. tomt är tomt: ingen fallback till "de senaste koncepten"
"""

from odoo.tests import common, tagged


@tagged('okf', 'post_install', '-at_install')
class TestOkfActionForOwner(common.TransactionCase):
    """F2.2: `ai.okf.concept._okf_action_for_owner()`."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Concept = cls.env['ai.okf.concept']

    def _owner_field(self, action):
        """Plocka ägarfältet ur åtgärdens domän (första villkoret)."""
        return action['domain'][0][0]

    # ── tre modeller → tre olika domäner ──

    def test_three_models_give_three_distinct_domains(self):
        """res.partner/res.company/res.users får inte dela domän."""
        company_id = self.env.company.id
        user_id = self.env.ref('base.user_admin').id
        coworker = self.env['ai.coworker'].create({
            'name': 'ZZ OKF Owner Action Coworker'})

        actions = {
            'res.company': self.Concept._okf_action_for_owner(
                'res.company', company_id),
            'res.users': self.Concept._okf_action_for_owner(
                'res.users', user_id),
            'ai.coworker': self.Concept._okf_action_for_owner(
                'ai.coworker', coworker.id),
        }

        for model, action in actions.items():
            self.assertTrue(action, '%s ska ge en åtgärd' % model)
            self.assertEqual(action['res_model'], 'ai.okf.concept')
            self.assertEqual(action['type'], 'ir.actions.act_window')

        fields = {m: self._owner_field(a) for m, a in actions.items()}
        self.assertEqual(fields['res.company'], 'owner_company_id')
        self.assertEqual(fields['res.users'], 'owner_user_id')
        self.assertEqual(fields['ai.coworker'], 'owner_coworker_id')
        # Tre modeller → tre olika domäner (ingen delad ägarfråga).
        self.assertEqual(len(set(fields.values())), 3)

    def test_domain_filters_on_the_owner_id(self):
        """Domänen filtrerar på postens id, inte ett annat id."""
        action = self.Concept._okf_action_for_owner('res.company', 4242)
        self.assertEqual(action['domain'][0], ('owner_company_id', '=', 4242))

    def test_coworker_domain_matches_owner_field(self):
        """ai.coworker-knappen filtrerar på owner_coworker_id."""
        coworker = self.env['ai.coworker'].create({
            'name': 'ZZ OKF Owner Action Coworker 2'})
        action = self.Concept._okf_action_for_owner(
            'ai.coworker', coworker.id)
        self.assertEqual(
            action['domain'][0], ('owner_coworker_id', '=', coworker.id))

    # ── okänd modell → False ──

    def test_unknown_model_gives_false(self):
        """En modell som inte är en OKF-ägare ger False, ingen krasch."""
        self.assertFalse(
            self.Concept._okf_action_for_owner('res.partner', 1))
        self.assertFalse(
            self.Concept._okf_action_for_owner('finns.inte.modell', 1))

    def test_invalid_res_id_gives_false(self):
        """Icke-numeriskt eller icke-positivt id ger False."""
        self.assertFalse(
            self.Concept._okf_action_for_owner('res.company', 'inte-ett-id'))
        self.assertFalse(
            self.Concept._okf_action_for_owner('res.company', 0))
        self.assertFalse(
            self.Concept._okf_action_for_owner('res.company', None))

    # ── create=False (ADD-only) ──

    def test_create_is_false_in_context(self):
        """Koncept är ADD-only — listan får inte erbjuda skapa-knappen."""
        action = self.Concept._okf_action_for_owner('res.company', 1)
        self.assertIs(action['context']['create'], False)

    # ── tomt är tomt ──

    def test_empty_owner_opens_empty_list(self):
        """En ägare utan koncept ger en tom domän — ingen fallback."""
        company = self.env['res.company'].create({
            'name': 'ZZ OKF Empty Owner Co'})
        action = self.Concept._okf_action_for_owner('res.company', company.id)
        self.assertEqual(
            action['domain'][0], ('owner_company_id', '=', company.id))
        self.assertEqual(
            self.Concept.search_count(action['domain']), 0)

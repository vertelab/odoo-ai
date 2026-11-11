# -*- coding: utf-8 -*-
"""Hela tidskedjan i drift (okf-temporal-recall task 11.6).

VARFÖR ETT INTEGRATIONSTEST: de enskilda bryggtesterna bevisar var sin
bit — att mailet har ett källdatum, att uppgiften registrerats, att
berikningen finns. Det här testet bevisar att BITARNA HÄNGER IHOP:

    ett mail från en kund
        → ett koncept med source_date
        → en sökning som hittar det
        → ett berikningsblock med uppgifter/möten i samma fönster

Det är den kedja användaren faktiskt möter, och den kan bara prövas när
bryggorna är installerade. Testet hoppar över sig självt om de saknas —
kärnan ska kunna köras utan dem.
"""

from datetime import datetime, timedelta

from odoo.tests import common, tagged


@tagged('okf', 'temporal', 'integration', 'post_install', '-at_install')
class TestTemporalChain(common.TransactionCase):
    """Mail + uppgift + möte i samma tidsfönster."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.Mixin = env['ai.okf.mixin']
        cls.Concept = env['ai.okf.concept']
        cls.company = env.company
        cls.partner = env['res.partner'].create({
            'name': 'Kedjetest Skanska AB'})
        cls.now = datetime.now()

    def setUp(self):
        super().setUp()
        from unittest.mock import patch
        self._embed_patch = patch.object(
            type(self.env['ai.provider']), '_embedding_provider',
            return_value=None)
        self._embed_patch.start()

    def tearDown(self):
        self._embed_patch.stop()
        super().tearDown()

    def _require(self, *models):
        missing = [m for m in models if m not in self.env]
        if missing:
            self.skipTest('saknar brygga: %s' % ', '.join(missing))

    def _mail(self, subject='Mail från Skanska'):
        """En riktig mail-post med kund — berikningen läser parten där.

        VARFÖR INTE ETT KONCEPT MED `source_ref='res.partner,N'`: en
        partner har inget `partner_id`-fält, så berikningen hittar ingen
        gemensam part. Parten bor på KÄLLPOSTEN (mailet), och det är den
        vägen som ska prövas.
        """
        return self.env['user_mail_ai.mail'].create({
            'message_id': '<chain-%s@test>' % (abs(hash(subject)) % 100000),
            'subject': subject,
            'from_email': 'kund@skanska-test.se',
            'from_name': 'Skanska',
            'received_at': self.now,
            'partner_id': self.partner.id,
            'user_id': self.env.user.id,
            'status': 'classified',
            'category': 'other',
        })

    def _concept(self, key, summary, source_date, role='received',
                 source=None):
        source_ref = ('user_mail_ai.mail,%s' % source.id) if source \
            else ('res.partner,%s' % self.partner.id)
        return self.Concept._okf_upsert(
            artifact_type='knowledge',
            concept_key=key,
            summary=summary,
            title=summary[:60],
            source_ref=source_ref,
            owner_company_id=self.company.id,
            source_date=source_date,
            temporal_role=role,
        )

    # ── Registreringarna finns ────────────────────────────────────────

    def test_all_bridges_registered(self):
        """Varje brygga har anmält sig — annars är kedjan halv."""
        self._require('user_mail_ai.mail', 'calendar.event',
                      'project.task', 'joplin.note')
        registry = self.Mixin._okf_related_models()
        for model in ('user_mail_ai.mail', 'calendar.event',
                      'project.task', 'project.project', 'joplin.note',
                      'res.partner'):
            self.assertIn(model, registry,
                          '%s saknas i berikningsregistret' % model)

    def test_defaults_point_in_opposite_directions(self):
        """Mail bakåt, kalender framåt — i samma sökning."""
        self._require('user_mail_ai.mail', 'calendar.event')
        defaults = self.Mixin._okf_temporal_defaults()
        self.assertEqual(defaults['mail']['direction'], 'backward')
        self.assertEqual(defaults['calendar_event']['direction'], 'forward')

    # ── Berikningen hittar rätt poster ───────────────────────────────

    def test_enrichment_finds_tasks_near_in_time(self):
        """En uppgift med samma kund i fönstret föreslås."""
        self._require('project.task', 'user_mail_ai.mail')
        project = self.env['project.project'].create({
            'name': 'Skanska-projekt', 'partner_id': self.partner.id})
        task = self.env['project.task'].create({
            'name': 'Offert Skanska Q4',
            'project_id': project.id,
            'partner_id': self.partner.id,
            'date_deadline': self.now + timedelta(days=2),
        })
        coworker = self.env['ai.coworker'].create({'name': 'Kedjetest'})
        mail = self._mail()
        mail_concept = self._concept(
            'chain.mail.1', 'Mail från Skanska om offert', self.now,
            source=mail)
        hits = [{'record': mail_concept, 'score': 1.0}]
        window = (self.now - timedelta(days=7), self.now + timedelta(days=7))
        text = coworker._related_enrich(hits, window=window)
        self.assertIn('project.task,%s' % task.id, text)
        self.assertIn('förslag', text.lower(),
                      'blocket ska märkas som förslag')

    def test_enrichment_ignores_other_customers(self):
        """En uppgift för en ANNAN kund är inte "nära"."""
        self._require('project.task')
        other = self.env['res.partner'].create({'name': 'Annan Kund AB'})
        project = self.env['project.project'].create({
            'name': 'Annans projekt', 'partner_id': other.id})
        task = self.env['project.task'].create({
            'name': 'Annan kunds uppgift',
            'project_id': project.id,
            'partner_id': other.id,
            'date_deadline': self.now + timedelta(days=1),
        })
        coworker = self.env['ai.coworker'].create({'name': 'Kedjetest 2'})
        mail = self._mail('Mail 2')
        mail_concept = self._concept(
            'chain.mail.2', 'Mail från Skanska', self.now, source=mail)
        hits = [{'record': mail_concept, 'score': 1.0}]
        window = (self.now - timedelta(days=7), self.now + timedelta(days=7))
        text = coworker._related_enrich(hits, window=window)
        self.assertNotIn('project.task,%s' % task.id, text,
                         'en annan kunds uppgift får inte föreslås')

    def test_enrichment_respects_the_window(self):
        """En uppgift utanför fönstret föreslås inte."""
        self._require('project.task')
        project = self.env['project.project'].create({
            'name': 'Gammalt projekt', 'partner_id': self.partner.id})
        task = self.env['project.task'].create({
            'name': 'Gammal uppgift',
            'project_id': project.id,
            'partner_id': self.partner.id,
            'date_deadline': self.now - timedelta(days=90),
        })
        coworker = self.env['ai.coworker'].create({'name': 'Kedjetest 3'})
        mail = self._mail('Mail 3')
        mail_concept = self._concept(
            'chain.mail.3', 'Mail från Skanska', self.now, source=mail)
        hits = [{'record': mail_concept, 'score': 1.0}]
        window = (self.now - timedelta(days=7), self.now + timedelta(days=7))
        text = coworker._related_enrich(hits, window=window)
        self.assertNotIn('project.task,%s' % task.id, text)

    def test_enrichment_can_be_disabled(self):
        """Avstängd berikning ger inget block (search-orchestration)."""
        coworker = self.env['ai.coworker'].create({
            'name': 'Kedjetest 4', 'related_enrichment': False})
        mail = self._mail('Mail 4')
        mail_concept = self._concept(
            'chain.mail.4', 'Mail från Skanska', self.now, source=mail)
        hits = [{'record': mail_concept, 'score': 1.0}]
        window = (self.now - timedelta(days=7), self.now + timedelta(days=7))
        self.assertEqual(coworker._related_enrich(hits, window=window), '')

    # ── Sökningen hittar mailet, berikningen lägger till resten ──────

    def test_search_and_enrichment_are_separate(self):
        """Sökningens `limit` rörs inte av berikningen (D11)."""
        self._require('project.task')
        project = self.env['project.project'].create({
            'name': 'Limit-projekt', 'partner_id': self.partner.id})
        for i in range(3):
            self.env['project.task'].create({
                'name': 'Limituppgift %s' % i,
                'project_id': project.id,
                'partner_id': self.partner.id,
                'date_deadline': self.now + timedelta(days=i + 1),
            })
        mail = self._mail('Mail 5')
        mail_concept = self._concept(
            'chain.mail.5', 'Skanska limit-test', self.now, source=mail)
        hits = self.Concept._okf_search(
            'Skanska limit-test', scope='company',
            owner_id=self.company.id, limit=1, semantic=False)
        self.assertLessEqual(len(hits), 1, 'limit ska hållas')
        coworker = self.env['ai.coworker'].create({'name': 'Kedjetest 5'})
        window = (self.now - timedelta(days=7), self.now + timedelta(days=7))
        text = coworker._related_enrich(
            [{'record': mail_concept, 'score': 1.0}], window=window)
        self.assertIn('project.task', text,
                      'berikningen lägger till vid sidan av sökresultatet')

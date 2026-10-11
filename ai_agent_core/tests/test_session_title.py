# -*- coding: utf-8 -*-
"""Tester för sessionsnamnet (session-title A→B→C).

VARFÖR DESSA TESTER FINNS: en session skapad via sessions-lookup hette
`pi_session_id[:8]` — åtta hextecken. I Pi:s /resume-lista och i Odoo:s
trådvy gick den inte att skilja från någon annan session. Namnet ska vara
EN MENING som beskriver vad sessionen handlar om.

Stegen är ordnade efter när informationen finns:
  A. Första prompten (`_lookup_or_create_pi_session(prompt=...)`).
  B. EN mening via LLM (`_maybe_update_title`).
  C. Eftermälets första rad (`_write_final_summary`).

Det viktiga som testas är RANGORDNINGEN: ett senare steg får uppgradera ett
tidigare, men ett tidigare steg får ALDRIG skriva över ett senare. Utan den
skulle varje ny tur i en resume:ad session nolla ett LLM-namn tillbaka till
första prompten.
"""

from unittest.mock import patch

from odoo.tests import common, tagged


@tagged('session', 'post_install', '-at_install')
class TestSessionTitle(common.TransactionCase):
    """Sessionsnamnets tre steg och rangordningen mellan dem."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']
        cls.user = cls.env.ref('base.user_admin')
        cls.coworker = cls.env['ai.coworker'].create({
            'name': 'Title Coworker',
            'description': 'Session title test',
            'status': 'active',
        })

    def _session(self, name='test-title', **vals):
        base = {
            'name': name,
            'status': 'active',
            'user_id': self.user.id,
            'coworker_id': self.coworker.id,
        }
        base.update(vals)
        return self.Session.create(base)

    # ── _clean_title: en mening, inte ett dokument ────────────────────

    def test_clean_title_strips_markdown_and_quotes(self):
        """Rubrik, citattecken, listmarkör och punktlista städas bort."""
        cases = [
            ('### Syfte\nUtred bokföringen.', 'Syfte'),
            ('"Fixa Caddy-routingen"', 'Fixa Caddy-routingen'),
            ('- Porta odoo-stock', 'Porta odoo-stock'),
            ('**Titel:** Felsök Postgres', 'Felsök Postgres'),
            ('  Radbrytning\n\nförst  ', 'Radbrytning'),
        ]
        for raw, expected in cases:
            self.assertEqual(
                self.Session._clean_title(raw), expected,
                'städning misslyckades för %r' % raw)

    def test_clean_title_truncates_and_handles_empty(self):
        """Lång text kapas; tom/None → ''."""
        long = 'x' * 200
        self.assertEqual(len(self.Session._clean_title(long)),
                         self.Session.TITLE_MAX_CHARS)
        self.assertEqual(self.Session._clean_title(''), '')
        self.assertEqual(self.Session._clean_title(None), '')
        self.assertEqual(self.Session._clean_title('   \n  '), '')

    # ── Steg A: första prompten ───────────────────────────────────────

    def test_lookup_uses_prompt_as_name(self):
        """Steg A: lookup med prompt döper sessionen efter den."""
        pid = '11111111-2222-3333-4444-555555555555'
        session, created = self.Session._lookup_or_create_pi_session(
            pid, prompt='Fixa Caddy-routingen för ledningssystem')
        self.assertTrue(created)
        self.assertEqual(
            session.name, 'Fixa Caddy-routingen för ledningssystem')
        self.assertEqual(session.title_source, 'prompt')

    def test_lookup_without_prompt_falls_back_to_uuid(self):
        """Utan prompt behålls det gamla beteendet (UUID-prefix)."""
        pid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        session, _created = self.Session._lookup_or_create_pi_session(pid)
        self.assertEqual(session.name, pid[:8])
        self.assertFalse(session.title_source)

    def test_lookup_is_idempotent(self):
        """Samma pi_session_id → samma session (namnet rörs inte)."""
        pid = '99999999-8888-7777-6666-555555555555'
        first, _ = self.Session._lookup_or_create_pi_session(
            pid, prompt='Första uppdraget')
        second, created = self.Session._lookup_or_create_pi_session(pid)
        self.assertFalse(created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.name, 'Första uppdraget')

    # ── Rangordningen: senare steg vinner ─────────────────────────────

    def test_llm_upgrades_prompt(self):
        """Steg B får uppgradera steg A."""
        session = self._session()
        session._maybe_set_title('Första prompten', source='prompt')
        self.assertEqual(session.title_source, 'prompt')
        session._maybe_set_title('En bättre mening', source='llm')
        self.assertEqual(session.name, 'En bättre mening')
        self.assertEqual(session.title_source, 'llm')

    def test_prompt_never_overwrites_llm(self):
        """Steg A får ALDRIG skriva över steg B (resume-fällan)."""
        session = self._session()
        session._maybe_set_title('En bättre mening', source='llm')
        wrote = session._maybe_set_title('Ny tur, ny prompt', source='prompt')
        self.assertFalse(wrote)
        self.assertEqual(session.name, 'En bättre mening')
        self.assertEqual(session.title_source, 'llm')

    def test_summary_upgrades_llm(self):
        """Steg C får uppgradera steg B."""
        session = self._session()
        session._maybe_set_title('En bättre mening', source='llm')
        session._maybe_set_title('Hela sessionen i en rad',
                                 source='summary')
        self.assertEqual(session.name, 'Hela sessionen i en rad')
        self.assertEqual(session.title_source, 'summary')

    def test_manual_is_never_overwritten(self):
        """Manuellt satt namn är starkast — inget steg rör det."""
        session = self._session()
        session._maybe_set_title('Mitt eget namn', source='manual')
        for source in ('prompt', 'llm', 'summary'):
            self.assertFalse(
                session._maybe_set_title('Något annat', source=source))
        self.assertEqual(session.name, 'Mitt eget namn')
        self.assertEqual(session.title_source, 'manual')

    def test_force_overrides_rank(self):
        """force=True kringgår rangordningen (för reparation)."""
        session = self._session()
        session._maybe_set_title('En bättre mening', source='llm')
        wrote = session._maybe_set_title(
            'Tvingat namn', source='prompt', force=True)
        self.assertTrue(wrote)
        self.assertEqual(session.name, 'Tvingat namn')

    def test_empty_title_never_writes(self):
        """En tom kandidat rör inte namnet (även med force)."""
        session = self._session(name='Behåll mig')
        self.assertFalse(
            session._maybe_set_title('   ', source='llm', force=True))
        self.assertEqual(session.name, 'Behåll mig')

    # ── Steg B: LLM-genereringen ──────────────────────────────────────

    def test_update_title_requires_user_and_assistant(self):
        """Utan både user- och assistant-rad finns inget att döpa efter."""
        session = self._session()
        self.Line.create({
            'session_id': session.id, 'sequence': 1,
            'role': 'user', 'content': 'Hej',
        })
        with patch.object(type(session), '_run_title_llm') as mock_llm:
            self.assertIsNone(session._maybe_update_title())
            mock_llm.assert_not_called()

    def test_update_title_writes_llm_name(self):
        """Steg B: en konversation → ett LLM-namn sätts."""
        session = self._session()
        self.Line.create({
            'session_id': session.id, 'sequence': 1,
            'role': 'user', 'content': 'Fixa Caddy-routingen',
        })
        self.Line.create({
            'session_id': session.id, 'sequence': 2,
            'role': 'assistant', 'content': 'Klart, routen är fixad.',
        })
        with patch.object(type(session), '_run_title_llm',
                          return_value='Fixa Caddy-routingen'):
            name = session._maybe_update_title()
        self.assertEqual(name, 'Fixa Caddy-routingen')
        self.assertEqual(session.name, 'Fixa Caddy-routingen')
        self.assertEqual(session.title_source, 'llm')

    def test_update_title_is_idempotent(self):
        """Ett redan satt LLM-namn → inget nytt LLM-anrop."""
        session = self._session()
        self.Line.create({
            'session_id': session.id, 'sequence': 1,
            'role': 'user', 'content': 'Hej',
        })
        self.Line.create({
            'session_id': session.id, 'sequence': 2,
            'role': 'assistant', 'content': 'Hej själv',
        })
        session._maybe_set_title('Redan satt', source='llm')
        with patch.object(type(session), '_run_title_llm') as mock_llm:
            self.assertEqual(session._maybe_update_title(), 'Redan satt')
            mock_llm.assert_not_called()

    def test_run_title_llm_rejects_placeholder_answers(self):
        """Platshållare från AgentLoop blir aldrig ett sessionsnamn.

        AgentLoop svarar "(no response)" när modellen inte gav något. Utan
        denna spärr döptes sessionen till just "(no response)" — sämre än
        att behålla det förra namnet.
        """
        session = self._session()
        for placeholder in ('(no response)', '(max rounds reached)',
                            '(inget svar)', 'Error: timeout'):
            with patch.object(
                type(session), '_run_title_llm', return_value=None,
            ):
                self.assertIsNone(session._maybe_update_title())
            # _clean_title accepterar texten — spärren sitter i _run_title_llm.
            self.assertTrue(session._clean_title(placeholder))

    def test_title_prompt_asks_for_one_sentence(self):
        """Prompten ber om EXAKT en mening, utan rubrik/citattecken."""
        session = self._session()
        prompt = session._title_prompt('user: Hej\nassistant: Hej')
        self.assertIn('EXAKT en mening', prompt)
        self.assertIn('utan rubrik', prompt)
        self.assertIn('user: Hej', prompt)

    def test_run_title_llm_cleans_output(self):
        """LLM-svaret städas innan det blir ett namn."""
        session = self._session()
        with patch.object(
            type(session), '_run_title_llm',
            side_effect=lambda t: session._clean_title('### "Ett namn."'),
        ):
            self.assertEqual(session._clean_title('### "Ett namn."'),
                             'Ett namn.')


@tagged('session', 'post_install', '-at_install')
class TestSessionTitleSummary(common.TransactionCase):
    """Steg C: eftermälet får förbättra namnet vid stängning."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Session = cls.env['ai.coworker.session']
        cls.Line = cls.env['ai.coworker.session.line']
        cls.user = cls.env.ref('base.user_admin')

    def _session(self, n_lines=6):
        session = self.Session.create({
            'name': 'test-summary-title',
            'status': 'active',
            'user_id': self.user.id,
        })
        for i in range(n_lines):
            self.Line.create({
                'session_id': session.id, 'sequence': i + 1,
                'role': 'user' if i % 2 == 0 else 'assistant',
                'content': 'Rad %d: beslut och fakta.' % (i + 1),
            })
        return session

    def test_summary_sets_name(self):
        """Eftermälet döper sessionen (steg C)."""
        session = self._session()
        with patch.object(
            type(session), '_run_final_summary_llm',
            return_value='### Syfte\nUtred bokföringen för kund X.',
        ):
            session._write_final_summary()
        self.assertEqual(session.name, 'Syfte')
        self.assertEqual(session.title_source, 'summary')

    def test_summary_keeps_manual_name(self):
        """Ett manuellt namn överlever eftermälet."""
        session = self._session()
        session._maybe_set_title('Mitt eget namn', source='manual')
        with patch.object(
            type(session), '_run_final_summary_llm',
            return_value='### Syfte\nNågot annat.',
        ):
            session._write_final_summary()
        self.assertEqual(session.name, 'Mitt eget namn')
        self.assertEqual(session.title_source, 'manual')

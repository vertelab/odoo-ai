# -*- coding: utf-8 -*-
"""Karaktäriseringstester för chat-kanalen (channel-adapter-wiring §1).

Ändring: channel-adapter-wiring (2026-11-02), tasks 1.3–1.4 (och 5.3–5.4).

VARFÖR: `ai.coworker.chat()` prövas mot det gemensamma
`process_item()`-kontraktet. Testet fångar beteendet som det ser ut i dag,
så att en migrering — eller en medveten icke-migrering — kan avgöras på
mätning i stället för på tro.

Det som mäts:

  1. `@mention` av boten rensas ur frågan (den ska inte ingå i prompten)
  2. historik-kontexten (tidigare meddelanden i kanalen) läggs i
     systemprompten under `## Recent conversation`
  3. cache-snabbspåret: en identisk tidigare fråga ger ett sparat svar —
     inget `run()`-anrop, men en session + två rader + ett kanal-inlägg
  4. sessionen skapas med `init_type='chat'` och namnet `Chat: <fråga>`
  5. svaret postas som botens partner (inte som frågaren — annars loopar
     routingen)
  6. `response_mode='trigger'` utan träfford → `None`, inget `run()`;
     med träfford → körningen fortsätter
  7. ett fel i `run()` → `status='error'`, `finish_reason='error'`

Inga LLM-anrop görs: `run()` patchas.
"""

from unittest.mock import patch

from odoo.tests.common import TransactionCase

from odoo.addons.ai_agent_core.models import ai_coworker as coworker_module


class ChatChannelCharacterization(TransactionCase):
    """Beteendet hos `ai.coworker.chat()` — mätt före migreringen."""

    def setUp(self):
        super().setUp()
        self.coworker = self.env['ai.coworker'].create({
            'name': 'Chatt-kanal karaktärisering',
            'status': 'active',
            'orchestration_mode': 'single',
        })
        self.channel = self.env['discuss.channel'].with_context(
            mail_create_nosubscribe=True).create({
                'name': 'Karaktäriseringskanal',
                'channel_type': 'channel',
            })
        self.bot_user = self.env['res.users'].create({
            'name': 'Karaktäriseringsbot',
            'login': 'karaktarisering_bot_%s' % self.env.uid,
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        self.message = self.env['mail.message'].create({
            'model': 'discuss.channel',
            'res_id': self.channel.id,
            'message_type': 'comment',
            'body': '<p>Vad är momsen på böcker?</p>',
            'author_id': self.env.user.partner_id.id,
        })

    # ── hjälpare ────────────────────────────────────────────────────

    def _chat(self, message=None, result='Momsen är 6 procent.',
              side_effect=None, system_prompts=None):
        """Kör chat() med patchad run().

        Kanalen skickas explicit — det är `_route_message()`:s kontrakt
        (`chat(message, channel=channel, bot_user=bot_user)`).

        Returnerar (returvärde, run-anrop).
        """
        run_calls = []
        captured_prompts = system_prompts if system_prompts is not None else []

        def _fake_run(prompt, session=None, **kwargs):
            run_calls.append({'prompt': prompt, 'session': session,
                              'kwargs': kwargs})
            captured_prompts.append(kwargs.get('system_prompt'))
            if side_effect is not None:
                raise side_effect
            self.env['ai.coworker.session.line'].create({
                'session_id': session.id,
                'sequence': 2,
                'role': 'assistant',
                'content': result,
            })
            return result

        with patch.object(type(self.coworker), 'run', _fake_run):
            value = self.coworker.chat(message or self.message,
                                       channel=self.channel,
                                       bot_user=self.bot_user)
        return value, run_calls

    def _chat_init(self):
        """Coworkerns chat-init_type, aktiverad.

        `enabled=True` krävs: `chat()` läser bara AKTIVA chat/channel-typer
        när den avgör response_mode och historik. Aktiveringen skapar också
        bot-användaren (`_ensure_chat_user`), som är den som svaret postas
        som.
        """
        init = self.coworker.init_type_ids.filtered(
            lambda it: it.init_type in ('chat', 'channel'))[:1]
        self.assertTrue(init, 'coworkern ska ha en chat/channel-init_type')
        if not init.enabled:
            init.enabled = True
        return init

    def _set_response_mode(self, mode, trigger_words=''):
        init = self._chat_init()
        init.write({'response_mode': mode,
                    'chat_trigger_words': trigger_words})
        return init

    # ── 1.3 — mention, historik, session, postning ──────────────────

    def test_session_is_created_with_init_type_chat(self):
        """Sessionen skapas med init_type='chat' och namnet efter frågan."""
        _value, _run_calls = self._chat()
        session = self.env['ai.coworker.session'].search(
            [('coworker_id', '=', self.coworker.id)],
            order='id desc', limit=1)
        self.assertEqual(session.init_type, 'chat')
        self.assertEqual(session.name, 'Chat: Vad är momsen på böcker?')

    def test_bot_mention_is_stripped_from_the_prompt(self):
        """@mention av boten rensas bort ur frågan."""
        self.message.body = '<p>@%s Vad är momsen på böcker?</p>' % (
            self.bot_user.name)
        _value, run_calls = self._chat()
        self.assertEqual(run_calls[0]['prompt'],
                         'Vad är momsen på böcker?')

    def test_history_is_added_to_the_system_prompt(self):
        """Tidigare kanalmeddelanden hamnar i '## Recent conversation'."""
        init = self._chat_init()
        init.write({'use_chat_history': True, 'chat_history_limit': 10})
        self.env['mail.message'].create({
            'model': 'discuss.channel',
            'res_id': self.channel.id,
            'message_type': 'comment',
            'body': '<p>Tidigare fråga om bokföring</p>',
            'author_id': self.env.user.partner_id.id,
        })
        prompts = []
        _value, run_calls = self._chat(system_prompts=prompts)
        self.assertEqual(run_calls[0]['prompt'],
                         'Vad är momsen på böcker?')
        # Historiken ligger i den systemprompt chat() bygger och skickar
        # vidare till run().
        self.assertTrue(prompts, 'run() ska få en systemprompt')
        self.assertIn('## Recent conversation', prompts[0])
        self.assertIn('Tidigare fråga om bokföring', prompts[0])

    def test_history_is_absent_when_disabled(self):
        """Utan use_chat_history finns ingen historik i systemprompten."""
        init = self._chat_init()
        init.write({'use_chat_history': False})
        self.env['mail.message'].create({
            'model': 'discuss.channel',
            'res_id': self.channel.id,
            'message_type': 'comment',
            'body': '<p>Tidigare fråga om bokföring</p>',
            'author_id': self.env.user.partner_id.id,
        })
        prompts = []
        self._chat(system_prompts=prompts)
        self.assertNotIn('## Recent conversation', prompts[0] or '')

    def test_answer_is_posted_as_the_bot_partner(self):
        """Svaret postas med botens partner som author."""
        _value, _run_calls = self._chat(result='Sex procent på böcker.')
        posted = self.env['mail.message'].search([
            ('model', '=', 'discuss.channel'),
            ('res_id', '=', self.channel.id),
            ('author_id', '=', self.bot_user.partner_id.id),
        ])
        self.assertEqual(len(posted), 1, 'exakt ett bot-inlägg')
        self.assertIn('Sex procent på böcker.', posted.body)

    def test_empty_question_returns_none(self):
        """En fråga som bara är en @mention blir tom → None."""
        self.message.body = '<p>@%s</p>' % self.bot_user.name
        value, run_calls = self._chat()
        self.assertIsNone(value)
        self.assertEqual(run_calls, [])

    def test_failure_sets_status_and_finish_reason_error(self):
        """Ett fel i run() → status='error', finish_reason='error'."""
        value, run_calls = self._chat(side_effect=RuntimeError('modell saknas'))
        self.assertIsNone(value)
        self.assertEqual(len(run_calls), 1)
        session = self.env['ai.coworker.session'].search(
            [('coworker_id', '=', self.coworker.id)],
            order='id desc', limit=1)
        self.assertEqual(session.status, 'error')
        self.assertEqual(session.finish_reason, 'error')
        self.assertIn('modell saknas', session.error_detail)

    # ── 1.3 — cache-snabbspåret ─────────────────────────────────────

    def test_cache_hit_skips_run_and_posts_the_cached_answer(self):
        """En identisk tidigare fråga besvaras ur cachen — utan run()."""
        # Första turen: skapar sessionsraden som cachen läser.
        self._chat(result='Sex procent på böcker.')
        # Andra turen: samma fråga, ny fråga-post i kanalen.
        second = self.env['mail.message'].create({
            'model': 'discuss.channel',
            'res_id': self.channel.id,
            'message_type': 'comment',
            'body': '<p>Vad är momsen på böcker?</p>',
            'author_id': self.env.user.partner_id.id,
        })
        value, run_calls = self._chat(message=second)
        self.assertEqual(run_calls, [],
                         'cache-träff ska inte kalla på run()')
        self.assertEqual(value, 'Sex procent på böcker.')
        # Cache-träffen skapar en egen session med två rader.
        sessions = self.env['ai.coworker.session'].search(
            [('coworker_id', '=', self.coworker.id)])
        self.assertEqual(len(sessions), 2,
                         'cache-träffen ska spåras som en egen session')
        cached_session = sessions.sorted('id')[-1]
        self.assertEqual(cached_session.status, 'done')
        self.assertEqual(len(cached_session.session_line_ids), 2)

    def test_cache_does_not_match_a_different_question(self):
        """En annan fråga får inte cache-träff."""
        self._chat(result='Sex procent på böcker.')
        other = self.env['mail.message'].create({
            'model': 'discuss.channel',
            'res_id': self.channel.id,
            'message_type': 'comment',
            'body': '<p>Hur bokför jag en hyresavi?</p>',
            'author_id': self.env.user.partner_id.id,
        })
        _value, run_calls = self._chat(message=other, result='Ett nytt svar.')
        self.assertEqual(len(run_calls), 1,
                         'en ny fråga ska köras genom run()')

    # ── 1.4 — response_mode='trigger' ───────────────────────────────

    def test_trigger_mode_without_trigger_word_returns_none(self):
        """trigger-läge utan träfford → None, inget run()."""
        self._set_response_mode('trigger', 'faktura, moms')
        self.message.body = '<p>Hur mår du idag?</p>'
        value, run_calls = self._chat()
        self.assertIsNone(value)
        self.assertEqual(run_calls, [], 'run() ska inte anropas')

    def test_trigger_mode_with_trigger_word_runs(self):
        """trigger-läge med träfford → körningen fortsätter."""
        self._set_response_mode('trigger', 'faktura, moms')
        self.message.body = '<p>Vad är momsen på böcker?</p>'
        value, run_calls = self._chat()
        self.assertEqual(value, 'Momsen är 6 procent.')
        self.assertEqual(len(run_calls), 1)

    def test_trigger_mode_without_words_returns_none(self):
        """trigger-läge utan konfigurerade träfford → None."""
        self._set_response_mode('trigger', '')
        value, run_calls = self._chat()
        self.assertIsNone(value)
        self.assertEqual(run_calls, [])

    def test_always_mode_runs_without_triggers(self):
        """'always' kräver inga träfford."""
        self._set_response_mode('always')
        value, run_calls = self._chat()
        self.assertEqual(value, 'Momsen är 6 procent.')
        self.assertEqual(len(run_calls), 1)

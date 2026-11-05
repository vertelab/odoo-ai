# -*- coding: utf-8 -*-
"""Regressionstester för `Message.role` som sträng (fix-message-role-string).

Felklass: **otypad dataclass-konstruktion** — ett fält deklareras som enum
men tar en sträng utan att protestera, och kraschar först vid ett senare
tillfälle (serialiseringen). Samma klass som `AgentConfig.max_rounds=None`
(fix-stream-max-rounds-none), nu på ett annat fält.

Symptomet i drift (ledningssystem 2026-11-05, varje tur, varje session):

    WARNING ai_session: Namn: generering misslyckades för session 27912:
            'str' object has no attribute 'value'

Sessionsnamnen blev aldrig berättande — de stod kvar på sitt uuid-default.
Sju anropsplatser skickade `role='user'` och felet fångades i `except` och
loggades som varning, vilket gjorde det tyst.

Testet prövar **serialiseringen**, inte konstruktionen: det var
`to_openai()` som kraschade, och ett test som bara kontrollerar
`msg.role == Role.USER` hade inte fångat det.
"""

from odoo.tests import common

from odoo.addons.ai_agent_core.core.provider import Message, Role


class TestMessageRoleNormalization(common.TransactionCase):
    """4.1–4.3: normalisering, nekande och oförändrad enum."""

    def test_string_role_serializes_without_raising(self):
        """4.1: en sträng-roll ska serialiseras, inte kasta.

        Fallerar på koden före rättningen med exakt driftens fel:
        `AttributeError: 'str' object has no attribute 'value'`.
        """
        msg = Message(role='user', content='hej')
        self.assertEqual(msg.role, Role.USER)
        out = msg.to_openai()
        self.assertEqual(out['role'], 'user')
        self.assertEqual(out['content'], 'hej')

    def test_all_known_strings_normalize(self):
        """Alla fyra giltiga strängvärden ska bli sina enum-medlemmar."""
        for value, expected in (('system', Role.SYSTEM),
                                ('user', Role.USER),
                                ('assistant', Role.ASSISTANT),
                                ('tool', Role.TOOL)):
            msg = Message(role=value, content='x')
            self.assertEqual(
                msg.role, expected,
                'role=%r borde bli %s' % (value, expected))
            # Och serialiseringen ska ge tillbaka strängen
            self.assertEqual(msg.to_openai()['role'], value)

    def test_unknown_role_rejected_at_construction(self):
        """4.2: ett okänt rollvärde nekas vid konstruktionen.

        Inte vid serialiseringen — felet ska uppstå där det begicks.
        """
        with self.assertRaises(ValueError) as ctx:
            Message(role='uppdragsgivare', content='x')
        message = str(ctx.exception)
        self.assertIn('uppdragsgivare', message,
                      'felmeddelandet ska namnge det otillåtna värdet')
        self.assertIn('user', message,
                      'felmeddelandet ska lista de giltiga värdena')

    def test_enum_members_pass_through_unchanged(self):
        """4.3: en Role-medlem ska inte dubbelkonverteras eller förändras."""
        for member in (Role.SYSTEM, Role.USER, Role.ASSISTANT, Role.TOOL):
            msg = Message(role=member, content='x')
            self.assertIs(msg.role, member,
                          'enum-medlemmen ska vara samma objekt')

    def test_both_serializers_work_for_every_role(self):
        """Normaliseringen ska göra båda provider-formaten körbara."""
        cases = {
            Role.USER: ('user', 'user'),
            Role.ASSISTANT: ('assistant', 'assistant'),
            Role.SYSTEM: ('system', None),   # Anthropic hanterar system separat
            Role.TOOL: ('tool', 'user'),     # tool_result-form
        }
        for member, (openai_role, anthropic_role) in cases.items():
            # Sträng-vägen (den som var trasig) ska ge samma resultat
            msg = Message(role=member.value, content='x', tool_call_id='c1')
            self.assertEqual(msg.to_openai()['role'], openai_role,
                             'to_openai fel för %s' % member)
            anthropic = msg.to_anthropic()
            if anthropic_role is None:
                self.assertIsNone(anthropic,
                                  'to_anthropic borde ge None för %s' % member)
            else:
                self.assertEqual(anthropic['role'], anthropic_role,
                                 'to_anthropic fel för %s' % member)

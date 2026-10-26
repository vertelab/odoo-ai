# -*- coding: utf-8 -*-
"""Stilleståndsvarningen i OKF-indexeringen (fix-personal-memory-budget-scope D5).

VARFÖR: `_okf_cron_index_dirty()` fångar fel PER POST (`except Exception`),
så `ir_cron.failure_count` är 0 även när ingen post blir indexerad. Cronen
"lyckas" varje varv medan kön står still. Mätt i drift 2026-10-25: 3 dygn
3 timmar utan en enda personal-post, `failure_count 0`.

Den ärliga signalen är åldern på den äldsta post som fortfarande är dirty.
En varning som upprepas 288 gånger per dygn (cron var 5:e minut) är brus,
inte ett larm — därför dedup per modell och dygn.

Varningen bor på `ai.okf.mixin`, som `ai.personal.memory` (via
`ai.memory.mixin`) ärver. `ai.okf.concept` bär den INTE — det är
konsumenten, inte en mixin-bärare.

Dessa tester bevisar:
  1. tröskeln läses ur `odoomind.personal_memory.stall_warn_hours`
  2. `0` stänger av varningen helt
  3. en ung dirty-post varnar inte
  4. en gammal dirty-post varnar
  5. varningen kommer EN gång per dygn per modell
  6. dedup-nyckeln stämplas med dagens datum
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import common, tagged

# Varningen loggas på MODULENS logger (ai_okf_mixin._logger), inte på en
# lokal instans — patcha rätt objekt, annars ser mocken ingenting.
from odoo.addons.ai_agent_core.models import ai_okf_mixin as _mixin_mod

STALL_PARAM = 'odoomind.personal_memory.stall_warn_hours'
MODEL = 'ai.personal.memory'


@tagged('okf', 'post_install', '-at_install')
class TestOkfStallWarning(common.TransactionCase):
    """D5: larma på ålder, inte på `failure_count`."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Memory = cls.env[MODEL]
        cls.user = cls.env['res.users'].create({
            'name': 'ZZ Stall User', 'login': 'zz_stall_user',
        })
        cls.params = cls.env['ir.config_parameter'].sudo()

    def setUp(self):
        super().setUp()
        self.params.set_param(STALL_PARAM, '24')
        self.params.set_param(
            'ai_agent_core.okf_stall_warned.%s' % MODEL, '')
        self.Memory.search([('content', 'like', 'zz-stall')]).unlink()

    def _dirty(self, age_hours):
        """Skapa en dirty-post och backdatera dess `write_date`."""
        record = self.Memory.create({
            'user_id': self.user.id,
            'content': 'zz-stall innehåll',
            'category': 'fact',
        })
        self.assertTrue(record.okf_dirty)
        stamp = fields.Datetime.now() - timedelta(hours=age_hours)
        self.env.cr.execute(
            'UPDATE ai_personal_memory SET write_date = %s WHERE id = %s',
            (stamp, record.id))
        record.invalidate_recordset(['write_date'])
        return record

    # ── tröskeln ────────────────────────────────────────────────────────

    def test_threshold_default_is_24_hours(self):
        """Default 24 timmar — ett dygn utan framsteg är stillestånd."""
        self.params.set_param(STALL_PARAM, '24')
        self.assertEqual(self.Memory._okf_stall_warn_hours(), 24)

    def test_threshold_invalid_value_falls_back_to_24(self):
        """Ett trasigt värde får inte krascha cronen."""
        self.params.set_param(STALL_PARAM, 'tjugofyra')
        self.assertEqual(self.Memory._okf_stall_warn_hours(), 24)

    def test_threshold_negative_is_clamped_to_zero(self):
        """Negativt värde blir 0 = avstängd, inte ett negativt fönster."""
        self.params.set_param(STALL_PARAM, '-5')
        self.assertEqual(self.Memory._okf_stall_warn_hours(), 0)

    # ── beteendet ───────────────────────────────────────────────────────

    def test_zero_threshold_disables_warning(self):
        """`0` stänger av varningen helt."""
        self.params.set_param(STALL_PARAM, '0')
        self._dirty(age_hours=1000)
        with patch.object(_mixin_mod._logger, 'warning') as warn:
            self.Memory._okf_check_stall(MODEL)
        self.assertFalse(warn.called)

    def test_young_record_does_not_warn(self):
        """En post under tröskeln är normal kö — ingen varning."""
        self._dirty(age_hours=2)
        with patch.object(_mixin_mod._logger, 'warning') as warn:
            self.Memory._okf_check_stall(MODEL)
        self.assertFalse(warn.called)

    def test_old_record_warns(self):
        """En post över tröskeln är stillestånd — varna."""
        self._dirty(age_hours=30)
        with patch.object(_mixin_mod._logger, 'warning') as warn:
            self.Memory._okf_check_stall(MODEL)
        self.assertTrue(warn.called)
        message = warn.call_args[0][0]
        self.assertIn('stillestånd', message)
        self.assertIn(MODEL, warn.call_args[0])

    def test_warning_is_deduplicated_per_day(self):
        """288 varv per dygn är brus — varningen kommer EN gång."""
        self._dirty(age_hours=30)
        with patch.object(_mixin_mod._logger, 'warning') as warn:
            self.Memory._okf_check_stall(MODEL)
            self.Memory._okf_check_stall(MODEL)
            self.Memory._okf_check_stall(MODEL)
        self.assertEqual(warn.call_count, 1)

    def test_dedup_key_is_stamped_with_today(self):
        """Nyckeln lagrar dygn, inte en boolean — nästa dygn varnar igen."""
        self._dirty(age_hours=30)
        with patch.object(_mixin_mod._logger, 'warning'):
            self.Memory._okf_check_stall(MODEL)
        stamped = self.params.get_param(
            'ai_agent_core.okf_stall_warned.%s' % MODEL)
        self.assertEqual(stamped, fields.Date.today().isoformat())

    def test_empty_queue_does_not_warn(self):
        """Tom kö är inte stillestånd."""
        with patch.object(_mixin_mod._logger, 'warning') as warn:
            self.Memory._okf_check_stall(MODEL)
        self.assertFalse(warn.called)

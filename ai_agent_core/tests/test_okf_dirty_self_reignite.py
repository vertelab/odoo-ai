# -*- coding: utf-8 -*-
"""PENSIONERAD 2026-09-22 (okf-mixin F2.8).

Testet bevisade att `ai.memory.write()` inte självåtertände `okf_dirty`.
Flaggan finns inte längre på `ai.memory` — den pensionerades som
OKF-konsument (okf-mixin D8): modellen är agentens RAG-kapacitet, inte
kunskap.

Självåtertändningen testas nu på `ai.okf.mixin` i
`test_okf_mixin.py::TestOkfDirtyFlag`, där flaggan bor.

Behållet som tom fil med flit: historiken (38 versioner av `ai.memory,257`)
är värd att kunna spåra, och en borttagen fil hade tystat den.
"""

from odoo.tests import common, tagged


@tagged('okf', 'memory', 'post_install', '-at_install')
class TestOkfDirtySelfReignite(common.TransactionCase):
    """Platshållare — se modulens docstring."""

    def test_moved_to_okf_mixin(self):
        """Flaggan bor nu på ai.okf.mixin, inte på ai.memory."""
        self.assertIn('okf_dirty', self.env['ai.okf.mixin']._fields)
        self.assertNotIn('okf_dirty', self.env['ai.memory']._fields)

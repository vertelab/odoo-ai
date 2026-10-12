"""Identity links on ai.coworker (coworker-access-identity-sync).

Verifies that employee_id, chat_user_id and partner_id are synchronised
bidirectionally, that the sync is idempotent and loop-safe, and that a
conflict is logged.
"""
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestCoworkerIdentitySync(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Coworker = cls.env['ai.coworker']
        cls.Employee = cls.env['hr.employee']
        cls.Users = cls.env['res.users']

        # Two employees, each with their own user (and therefore partner).
        cls.user_a = cls.Users.create({
            'name': 'Identity Test A', 'login': 'ident_test_a'})
        cls.user_b = cls.Users.create({
            'name': 'Identity Test B', 'login': 'ident_test_b'})
        cls.emp_a = cls.Employee.create({
            'name': 'Employee A', 'user_id': cls.user_a.id})
        cls.emp_b = cls.Employee.create({
            'name': 'Employee B', 'user_id': cls.user_b.id})
        # An employee without a user.
        cls.emp_no_user = cls.Employee.create({'name': 'Employee no user'})

    def _coworker(self, **vals):
        vals.setdefault('name', 'Identity test coworker')
        return self.Coworker.create(vals)

    # -- 1.1 employee_id -> chat_user_id + partner_id --

    def test_employee_id_fills_user_and_partner(self):
        c = self._coworker(employee_id=self.emp_a.id)
        self.assertEqual(c.chat_user_id, self.user_a)
        self.assertEqual(c.partner_id, self.user_a.partner_id)

    def test_employee_without_user_leaves_fields_untouched(self):
        c = self._coworker(employee_id=self.emp_no_user.id)
        self.assertFalse(c.chat_user_id)
        self.assertFalse(c.partner_id)

    # -- 1.2 chat_user_id -> partner_id + employee_id --

    def test_chat_user_fills_partner_and_employee(self):
        c = self._coworker(chat_user_id=self.user_a.id)
        self.assertEqual(c.partner_id, self.user_a.partner_id)
        self.assertEqual(c.employee_id, self.emp_a)

    def test_user_without_employee_leaves_employee_untouched(self):
        user_alone = self.Users.create({
            'name': 'User without employee', 'login': 'ident_test_alone'})
        # Create the coworker first (create auto-assigns an hr.employee via
        # _ensure_employee), then isolate the sync through write.
        c = self._coworker()
        c.write({'employee_id': False})
        self.assertFalse(c.employee_id)
        c.write({'chat_user_id': user_alone.id})
        self.assertEqual(c.partner_id, user_alone.partner_id)
        self.assertFalse(c.employee_id, 'employee_id must be left untouched')

    # -- 1.3 idempotency --

    def test_sync_is_idempotent(self):
        c = self._coworker(employee_id=self.emp_a.id)
        before = (c.chat_user_id.id, c.partner_id.id)
        c.write({'employee_id': self.emp_a.id})
        self.assertEqual((c.chat_user_id.id, c.partner_id.id), before)

    # -- 2.1/2.2 loop and recursion safety --

    def test_write_does_not_recurse(self):
        c = self._coworker(employee_id=self.emp_a.id)
        # Must finish without RecursionError.
        c.write({'employee_id': self.emp_b.id})
        self.assertEqual(c.chat_user_id, self.user_b)

    def test_unrelated_write_does_not_touch_identity(self):
        c = self._coworker(employee_id=self.emp_a.id)
        before = (c.employee_id.id, c.chat_user_id.id, c.partner_id.id)
        c.write({'name': 'New name'})
        self.assertEqual(
            (c.employee_id.id, c.chat_user_id.id, c.partner_id.id), before)

    # -- 4.3 never write False over a set chat_user_id --

    def test_employee_without_user_does_not_clear_chat_user(self):
        c = self._coworker(employee_id=self.emp_a.id)
        self.assertTrue(c.chat_user_id)
        c.write({'employee_id': self.emp_no_user.id})
        self.assertTrue(c.chat_user_id, 'chat_user_id must not be cleared')

    # -- 5.1 conflict: last changed field wins --

    def test_last_changed_field_wins(self):
        c = self._coworker(employee_id=self.emp_a.id)
        self.assertEqual(c.chat_user_id, self.user_a)
        # Set employee_id to B -> B becomes authoritative.
        c.write({'employee_id': self.emp_b.id})
        self.assertEqual(c.employee_id, self.emp_b)
        self.assertEqual(c.chat_user_id, self.user_b)

    def test_chat_user_change_wins_over_employee(self):
        c = self._coworker(employee_id=self.emp_a.id)
        c.write({'chat_user_id': self.user_b.id})
        self.assertEqual(c.chat_user_id, self.user_b)
        self.assertEqual(c.employee_id, self.emp_b)

    # -- 5.2 conflict is logged --

    def test_conflict_is_logged(self):
        # Build a coworker whose employee_id and chat_user_id deliberately
        # point at DIFFERENT employees (via a guarded write, so the sync
        # does not level them out). Then write employee_id = A while
        # chat_user_id is already B -> a conflict that must be logged.
        c = self._coworker(employee_id=self.emp_a.id)
        c.with_context(skip_identity_sync=True).write({
            'chat_user_id': self.user_b.id})
        self.assertEqual(c.chat_user_id, self.user_b)
        self.assertEqual(c.employee_id, self.emp_a)
        with self.assertLogs('odoo.addons.ai_agent_core.models.ai_coworker',
                             level='WARNING') as cm:
            # employee_id = A but chat_user_id = B -> conflict.
            c.write({'employee_id': self.emp_a.id})
        self.assertTrue(
            any('Identity conflict' in m for m in cm.output),
            f'The conflict must be logged: {cm.output}')

    # -- 3.x onchange (form values, no persistence) --

    def test_onchange_employee_sets_form_values(self):
        c = self.Coworker.new({'name': 'Onchange test'})
        c.employee_id = self.emp_a
        c._onchange_employee_id_identity()
        self.assertEqual(c.chat_user_id, self.user_a)
        self.assertEqual(c.partner_id, self.user_a.partner_id)

    def test_onchange_chat_user_sets_form_values(self):
        c = self.Coworker.new({'name': 'Onchange test 2'})
        c.chat_user_id = self.user_a
        c._onchange_chat_user_id_identity()
        self.assertEqual(c.partner_id, self.user_a.partner_id)
        self.assertEqual(c.employee_id, self.emp_a)

    def test_onchange_chain_is_stable(self):
        c = self.Coworker.new({'name': 'Onchange test 3'})
        c.employee_id = self.emp_a
        c._onchange_employee_id_identity()
        first = (c.chat_user_id.id, c.partner_id.id)
        # Run again - must be stable, not oscillate.
        c._onchange_chat_user_id_identity()
        c._onchange_employee_id_identity()
        self.assertEqual((c.chat_user_id.id, c.partner_id.id), first)

    # -- 7.2 same end state regardless of path --

    def test_create_and_write_give_same_state(self):
        via_create = self._coworker(employee_id=self.emp_a.id)
        via_write = self._coworker()
        via_write.write({'employee_id': self.emp_a.id})
        self.assertEqual(via_create.chat_user_id, via_write.chat_user_id)
        self.assertEqual(via_create.partner_id, via_write.partner_id)
        self.assertEqual(via_create.employee_id, via_write.employee_id)

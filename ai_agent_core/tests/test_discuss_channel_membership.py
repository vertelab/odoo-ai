# -*- coding: utf-8 -*-
"""Kanalmedlemskap som läsbarhetskälla (discuss-channel-rights).

Ett OKF-koncept vars källa är ett Discuss-meddelande är läsbart för
kanalens medlemmar — och bara dem. Meddelandet kopplas till kanalen via
`model='discuss.channel'` + `res_id`, inte via `channel_id` (Odoo 18).
"""

from odoo.tests.common import TransactionCase


class TestChannelMembership(TransactionCase):
    """_filter_by_channel_membership — kärnan i discuss-channel-rights."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Concept = cls.env['ai.okf.concept']
        cls.member = cls.env['res.users'].create({
            'name': 'Kanalmedlem',
            'login': 'kanalmedlem_test',
            'email': 'medlem@example.com',
            'company_ids': [(6, 0, [cls.env.company.id])],
            'company_id': cls.env.company.id,
        })
        cls.outsider = cls.env['res.users'].create({
            'name': 'Utomstaende',
            'login': 'utomstaende_test',
            'email': 'utom@example.com',
            'company_ids': [(6, 0, [cls.env.company.id])],
            'company_id': cls.env.company.id,
        })
        cls.channel = cls.env['discuss.channel'].create({
            'name': 'Testkanal discuss',
            'channel_type': 'channel',
            'channel_partner_ids': [(6, 0, [cls.member.partner_id.id])],
        })
        cls.message = cls.env['mail.message'].create({
            'model': 'discuss.channel',
            'res_id': cls.channel.id,
            'body': 'Ett meddelande i kanalen',
            'message_type': 'comment',
        })

    def test_member_sees_message(self):
        """Medlemmen ser meddelandet."""
        visible = self.Concept._filter_by_channel_membership(
            {self.message.id}, self.member.id)
        self.assertIn(self.message.id, visible)

    def test_outsider_does_not_see_message(self):
        """Icke-medlemmen ser det inte."""
        visible = self.Concept._filter_by_channel_membership(
            {self.message.id}, self.outsider.id)
        self.assertNotIn(self.message.id, visible)

    def test_non_discuss_message_unaffected(self):
        """Chatter på en affärspost påverkas inte av kanalfiltret."""
        partner = self.env['res.partner'].create({'name': 'Chatterkund'})
        chatter_msg = self.env['mail.message'].create({
            'model': 'res.partner',
            'res_id': partner.id,
            'body': 'Chatter på partnern',
            'message_type': 'comment',
        })
        visible = self.Concept._filter_by_channel_membership(
            {chatter_msg.id}, self.outsider.id)
        self.assertIn(chatter_msg.id, visible)

    def test_empty_input(self):
        """Tom mängd ger tom mängd — ingen krasch."""
        self.assertEqual(
            self.Concept._filter_by_channel_membership(set(), self.member.id),
            set())

    def test_deleted_channel_excludes_message(self):
        """Meddelande vars kanal är borta utesluts — overifierbar källa."""
        channel = self.env['discuss.channel'].create({
            'name': 'Tillfallig kanal',
            'channel_type': 'channel',
            'channel_partner_ids': [(6, 0, [self.member.partner_id.id])],
        })
        msg = self.env['mail.message'].create({
            'model': 'discuss.channel',
            'res_id': channel.id,
            'body': 'Forsvinner',
            'message_type': 'comment',
        })
        channel.unlink()
        visible = self.Concept._filter_by_channel_membership(
            {msg.id}, self.member.id)
        self.assertNotIn(msg.id, visible)

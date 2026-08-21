from odoo.tests.common import TransactionCase


class TestImap(TransactionCase):

    def setUp(self):
        super().setUp()
        self.imap = self.env['user.mail.imap']
        self.user = self.env.user
        self.user.write({'password': 'test123'})

    def test_password_encryption_on_write(self):
        self.assertTrue(self.user.imap_password, "imap_password should be set after password write")
        decrypted = self.user._decrypt_imap_pw()
        self.assertEqual(decrypted, 'test123')

    def test_encryption_key_generated(self):
        key = self.env['ir.config_parameter'].get_param('user_mail_imap.encryption_key')
        self.assertTrue(key, "Encryption key should exist in config")

    def test_write_updates_imap_password(self):
        self.user.write({'password': 'newpass456'})
        decrypted = self.user._decrypt_imap_pw()
        self.assertEqual(decrypted, 'newpass456')

    def test_create_sets_imap_password(self):
        user = self.env['res.users'].create({
            'name': 'Test User',
            'login': 'test@example.com',
            'password': 'createpass',
        })
        self.assertTrue(user.imap_password, "imap_password should be set on create")
        decrypted = user._decrypt_imap_pw()
        self.assertEqual(decrypted, 'createpass')

    def test_encrypt_decrypt_roundtrip(self):
        pw = 'my_secret_password!123'
        encrypted = self.user._encrypt_imap_pw(pw)
        self.assertNotEqual(encrypted, pw, "Encrypted should differ from plaintext")
        self.user.imap_password = encrypted
        decrypted = self.user._decrypt_imap_pw()
        self.assertEqual(decrypted, pw)

    def test_empty_password_returns_none(self):
        self.user.imap_password = False
        result = self.user._decrypt_imap_pw()
        self.assertIsNone(result)

    def test_model_exists(self):
        self.assertTrue(self.imap, "user.mail.imap model should exist")
        self.assertEqual(self.imap._description, 'IMAP Mail Operations')

    def test_model_fields(self):
        user_fields = self.env['res.users'].fields_get()
        self.assertIn('imap_password', user_fields, "res.users should have imap_password field")

    # ── Poller (normalisering, dedup, modeller) ──────────────────────

    def _make_raw_email(self, message_id='<test-1@example.com>', subject='Hej',
                        frm='Anna <anna@example.com>', to='kalle@vertel.se',
                        body='Test body', add_ics=False):
        from email.message import EmailMessage
        msg = EmailMessage()
        msg['Message-ID'] = message_id
        msg['Subject'] = subject
        msg['From'] = frm
        msg['To'] = to
        msg['Date'] = 'Mon, 05 Aug 2026 10:00:00 +0200'
        msg.set_content(body)
        if add_ics:
            msg.add_attachment(
                'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n',
                filename='invite.ics', maintype='text', subtype='calendar')
        return msg.as_bytes()

    def test_normalize_message(self):
        raw = self._make_raw_email()
        norm = self.imap._normalize_message(raw, folder='INBOX')
        self.assertEqual(norm['message_id'], '<test-1@example.com>')
        self.assertEqual(norm['subject'], 'Hej')
        self.assertIn('anna@example.com', norm['from_'])
        self.assertEqual(norm['folder'], 'INBOX')
        self.assertIn('Test body', norm['body_text'])
        self.assertEqual(norm['dedup_key'], '<test-1@example.com>')

    def test_normalize_message_with_ics_attachment(self):
        raw = self._make_raw_email(add_ics=True)
        norm = self.imap._normalize_message(raw)
        self.assertTrue(any(
            a['filename'] == 'invite.ics' for a in norm['attachments']))

    def test_dedup_key_fallback_hash(self):
        norm = {
            'from_': 'anna@example.com',
            'subject': 'Hej',
            'date': 'Mon, 05 Aug 2026 10:00:00 +0200',
        }
        key = self.imap._dedup_key(norm)
        self.assertTrue(key.startswith('h:'), "Fallback key should be hashed")

    def test_processed_unique_constraint(self):
        user = self.env.user
        self.env['user.mail.processed'].create({
            'user_id': user.id, 'message_id': 'dup-1'})
        with self.assertRaises(Exception):
            self.env['user.mail.processed'].create({
                'user_id': user.id, 'message_id': 'dup-1'})

    def test_poll_model_and_fields(self):
        self.assertTrue(self.env['user.mail.poll'])
        self.assertTrue('imap_poll_enabled' in self.env['res.users']._fields)
        self.assertTrue('last_imap_sync' in self.env['res.users']._fields)
        self.assertFalse(self.env.user.imap_poll_enabled,
                         "Poll ska vara opt-in per användare")

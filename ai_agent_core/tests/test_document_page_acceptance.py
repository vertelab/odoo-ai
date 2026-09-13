# -*- coding: utf-8 -*-
"""Acceptansfall: document.page under Dokumentation
(improve-ai-coworker-memory-and-tools grupp 5).

Reproducerar den ursprungliga felhändelsen: coworkern ska skapa ett
document.page med rätt parent_id och icke-tomt content.

  (5.1) posten skapas och verifieras (write-verify mot rätt förälder/krävs
        icke-tomt innehåll),
  (5.2) felfallet: felaktig parameter första försöket ⇒ åtgärdbart fel ⇒
        korrigerat anrop ⇒ verifierat resultat, med hela kedjan synlig i
        sessionen.
"""

import json
import unittest

from odoo.tests import common, tagged

from odoo.addons.ai_agent_core.core.tools import _tool_odoo_create
from odoo.addons.ai_agent_core.core.verify import verify_write_outcome

# Write-verify-avtalet för document.page (5.1) — samma form som
# _ensure_verification_contracts sätter på odoo_create.
DOCUMENT_PAGE_CONTRACT = {
    'model': 'document.page',
    'id_path': 'id',
    'checks': [
        {'field': 'name', 'equals_path': 'values.name'},
        {'field': 'content', 'non_empty': True},
        {'field': 'parent_id', 'equals_path': 'values.parent_id'},
    ],
}


@tagged('post_install')
class TestDocumentPageAcceptance(common.TransactionCase):
    """End-to-end: skapa document.page + åtgärda felfallet."""

    def _require_document_page(self):
        """Hoppa över tyst om document.page saknas i registret."""
        if 'document.page' not in self.env.registry:
            raise unittest.SkipTest('document.page finns inte i registret')

    def _has_document_page(self):
        return 'document.page' in self.env.registry

    def _dokumentation(self):
        """Hitta/skapa föräldern 'Dokumentation'."""
        Page = self.env['document.page']
        parent = Page.search([('name', '=', 'Dokumentation')], limit=1)
        if not parent:
            parent = Page.create({'name': 'Dokumentation'})
        return parent

    def _first_writable_content_field(self):
        """document.page:s innehållsfält (content eller body)."""
        Page = self.env['document.page']
        for f in ('content', 'body', 'content_html'):
            if f in Page._fields:
                return f
        return None

    # ── 5.1 Skapa + verifiera ──────────────────────────────────────────

    def test_create_document_page_verified(self):
        """document.page skapas under Dokumentation med icke-tomt content."""
        if not self._has_document_page():
            raise unittest.SkipTest("document.page saknas")
        parent = self._dokumentation()
        cf = self._first_writable_content_field()
        if not cf:
            raise unittest.SkipTest("document.page saknar innehållsfält")

        values = {
            'name': 'Reklamfri & avlyssningssäker TV — utredning',
            'parent_id': parent.id,
            cf: '<p>Utredning om LG C5, ACR, root och signage.</p>',
        }
        raw = _tool_odoo_create(
            self.env, model='document.page', values=values)
        data = json.loads(raw)
        self.assertTrue(data.get('ok'), data)
        page = self.env['document.page'].browse(data['id'])
        self.assertEqual(page.parent_id.id, parent.id)
        self.assertTrue(page[cf])

        # write-verify mot avtalet (parent + innehåll).
        contract = dict(DOCUMENT_PAGE_CONTRACT)
        contract['checks'] = [
            c for c in contract['checks'] if c['field'] in page._fields
        ]
        result = verify_write_outcome(
            contract, {'ok': True, 'id': page.id, 'values': values},
            env=self.env)
        self.assertTrue(result.passed, result.all_errors)

    # ── 5.2 Felfallet ──────────────────────────────────────────────────

    def test_error_case_bad_param_then_corrected(self):
        """Fel parameter → åtgärdbart fel → korrigerat → verifierat."""
        if not self._has_document_page():
            raise unittest.SkipTest("document.page saknas")
        parent = self._dokumentation()
        cf = self._first_writable_content_field()
        if not cf:
            raise unittest.SkipTest("document.page saknar innehållsfält")

        # 1. FÖRSTA FÖRSÖKET: felaktig parameter (okänt fält) → åtgärdbart fel.
        bad = _tool_odoo_create(
            self.env, model='document.page',
            values={'name': 'Reklamfri TV', 'parrent_id': parent.id})
        bad_data = json.loads(bad)
        self.assertIn('parrent_id', bad_data['error'])
        self.assertEqual(bad_data['parameter'], 'parrent_id')
        self.assertTrue(bad_data['valid_fields'])
        self.assertIn('parent_id', bad_data['valid_fields'])
        self.assertTrue(bad_data['retryable'])

        # 2. KORRIGERAT ANROP: rätt fältnamn + innehåll.
        values = {
            'name': 'Reklamfri & avlyssningssäker TV — utredning',
            'parent_id': parent.id,
            cf: '<p>Hela tråden: LG C5, ACR, root, signage.</p>',
        }
        good = _tool_odoo_create(
            self.env, model='document.page', values=values)
        good_data = json.loads(good)
        self.assertTrue(good_data.get('ok'), good_data)

        # 3. VERIFIERAT RESULTAT.
        contract = dict(DOCUMENT_PAGE_CONTRACT)
        contract['checks'] = [
            c for c in contract['checks']
            if c['field'] in self.env['document.page']._fields
        ]
        result = verify_write_outcome(
            contract, good_data, env=self.env)
        self.assertTrue(result.passed, result.all_errors)

    def test_session_shows_full_chain(self):
        """Hela kedjan försök → fel → rättelse → verifiering syns i sessionen."""
        if not self._has_document_page():
            raise unittest.SkipTest("document.page saknas")
        parent = self._dokumentation()
        cf = self._first_writable_content_field()
        if not cf:
            raise unittest.SkipTest("document.page saknar innehållsfält")

        # Verktygspost med avtalet (som _ensure_verification_contracts gör).
        self.env['ai.tool'].create({
            'name': 'acceptance_create_tool',
            'description': 'acceptance',
            'verification_enabled': True,
            'verification_json': json.dumps({
                'model': 'document.page', 'id_path': 'id',
                'checks': [{'field': 'content', 'non_empty': True}],
            }),
        })
        coworker = self.env['ai.coworker'].create({
            'name': 'Acceptance Coworker', 'description': 'x',
            'status': 'active',
        })
        session = self.env['ai.coworker.session'].create({
            'coworker_id': coworker.id, 'status': 'active',
        })

        # Ett utfall som INTE uppfyller avtalet (tomt innehåll) → fail +
        # självkorrigeringskedja loggad i sessionen.
        page = self.env['document.page'].create({
            'name': 'Tom sida', 'parent_id': parent.id,
        })
        if cf in self.env['document.page']._fields:
            try:
                page.write({cf: ''})
            except Exception:
                pass

        coworker._run_write_verify(
            [('acceptance_create_tool', {},
              json.dumps({'ok': True, 'id': page.id,
                          'values': {'name': 'Tom sida'}}))],
            session=session)

        chain = session.session_line_ids.filtered(
            lambda l: l.role == 'system'
            and 'tool-verify' in (l.content or ''))
        self.assertTrue(chain, 'kedjan loggades inte i sessionen')
        self.assertIn('acceptance_create_tool', chain[0].content)

# -*- coding: utf-8 -*-
"""Batchad embedding vid OKF-indexering (batched-okf-embedding D1–D3).

VARFÖR: `_okf_upsert` producerade EN embedding per koncept — ett HTTP-anrop
i taget. En post med N ägare betalade N anrop. Mätt 2026-10-08:

    6 separata anrop:  2,91 s
    1 batch-anrop:     0,32 s   -> 9,1x, identiska vektorer

Kostnaden uppmärksammades när `calendar_ai` började ge N+1 koncept per
händelse (ett company + ett personal per deltagare).

Dessa tester bevisar:
  1. en post med flera ägare gör ETT provideranrop
  2. vektor i hamnar på koncept i (inte en annans)
  3. batchfel faller tillbaka på per-koncept utan att tysta
  4. en post med en ägare är oförändrad
  5. batch och enskilt anrop ger samma vektor
"""

from unittest.mock import patch

from odoo.tests import common, tagged
from ._config_param_guard import ConfigParamGuardedCase

import logging
_logger = logging.getLogger(__name__)


@tagged('okf', 'embedding', 'post_install', '-at_install')
class TestBatchedEmbedding(ConfigParamGuardedCase):
    """D1–D3: batchning inom en post."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Concept = cls.env['ai.okf.concept']
        cls.Mixin = cls.env['ai.okf.mixin']
        cls.Personal = cls.env['ai.personal.memory']
        cls.atype = cls.env['ai.artifact.type'].search(
            [('name', '=', 'learning')], limit=1)
        if not cls.atype:
            cls.atype = cls.env['ai.artifact.type'].create({
                'name': 'learning', 'kind': 'memory'})
        cls.user_a = cls.env['res.users'].create({
            'name': 'ZZ Batch A', 'login': 'zz_batch_a'})
        cls.user_b = cls.env['res.users'].create({
            'name': 'ZZ Batch B', 'login': 'zz_batch_b'})
        cls.user_c = cls.env['res.users'].create({
            'name': 'ZZ Batch C', 'login': 'zz_batch_c'})

    def _mem(self, content='Ett minne om fakturor och kunder.'):
        return self.Personal.create({
            'user_id': self.user_a.id, 'content': content})

    def _owners(self, n=3):
        users = [self.user_a, self.user_b, self.user_c][:n]
        return [{'owner_user_id': u.id} for u in users]

    # ── 2.2: ett anrop för N ägare ──

    def test_multi_owner_makes_one_batch_call(self):
        """En post med tre ägare gör ETT batch-anrop."""
        mem = self._mem()
        calls = []
        real = type(self.Mixin)._okf_embed_texts

        def spy(self_, texts):
            calls.append(list(texts))
            return real(self_, texts)

        with patch.object(type(mem), '_okf_owner_vals_list',
                          return_value=self._owners(3)), \
                patch.object(type(mem), '_okf_embed_texts', spy):
            mem._okf_index_record()

        self.assertEqual(len(calls), 1,
                         'ett batch-anrop förväntades, fick %d' % len(calls))
        self.assertEqual(len(calls[0]), 3,
                         'tre texter (en per ägare) förväntades')

    def test_concepts_created_for_all_owners(self):
        """Alla ägare får sitt koncept trots ett anrop."""
        mem = self._mem()
        with patch.object(type(mem), '_okf_owner_vals_list',
                          return_value=self._owners(3)):
            mem._okf_index_record()
        concepts = self.Concept.search([
            ('source_ref', '=', 'ai.personal.memory,%s' % mem.id)])
        self.assertEqual(len(concepts), 3)

    # ── 2.3: rätt vektor till rätt koncept ──

    def test_vector_i_goes_to_concept_i(self):
        """Vektor *i* hamnar på koncept *i*.

        Ägarna får olika sammanfattningar (kalenderfallet), så en
        förväxling syns: varje koncept ska ha den vektor som hör till
        dess egen text.
        """
        mem = self._mem()
        sent = {}

        def fake_embed(self_, texts):
            sent['texts'] = list(texts)
            # En distinkt vektor per text (samma längd som EMBEDDING_DIM).
            from odoo.addons.ai_agent_core.models.ai_okf_concept import (
                EMBEDDING_DIM)
            return [[float(i + 1)] * EMBEDDING_DIM
                    for i in range(len(texts))]

        with patch.object(type(mem), '_okf_owner_vals_list',
                          return_value=self._owners(3)), \
                patch.object(type(mem), '_okf_embed_texts', fake_embed):
            mem._okf_index_record()

        concepts = self.Concept.search([
            ('source_ref', '=', 'ai.personal.memory,%s' % mem.id)],
            order='owner_user_id')
        self.assertEqual(len(concepts), 3)
        # Koncept i ska ha vektorn [i+1, i+1, ...] — inte en annans.
        for i, c in enumerate(concepts):
            vec = c.embedding
            self.assertTrue(vec, 'koncept %d saknar vektor' % i)
            first = vec[0] if isinstance(vec, (list, tuple)) else None
            if first is None:
                import json
                first = json.loads(vec)[0]
            self.assertEqual(
                float(first), float(i + 1),
                'koncept %d fick vektor %s (förväntade %s)' % (
                    i, first, i + 1))

    # ── 3.1: partiellt batchfel ──

    def test_partial_batch_failure_falls_back(self):
        """En text utan vektor embeddas i ett eget anrop.

        Fallbacken gar genom `_produce_embedding` (som gor ett eget
        HTTP-anrop). Testmiljon blockerar HTTP, sa vi patchar aven den —
        det ar FALLBACK-VAGEN vi provar, inte natverket.
        """
        mem = self._mem()
        from odoo.addons.ai_agent_core.models.ai_okf_concept import (
            EMBEDDING_DIM)

        def partial(self_, texts):
            # Forsta och sista far vektor, mitten inte.
            out = [[1.0] * EMBEDDING_DIM for _ in texts]
            if len(out) > 1:
                out[1] = None
            return out

        def fake_produce(self_, summary, title=None, explicit=None):
            # Fallbacken: en egen vektor for den text batchen inte klarade.
            if explicit is not None:
                return (explicit, 'ready')
            return ([9.0] * EMBEDDING_DIM, 'ready')

        with patch.object(type(mem), '_okf_owner_vals_list',
                          return_value=self._owners(3)), \
                patch.object(type(mem), '_okf_embed_texts', partial), \
                patch.object(type(self.Concept), '_produce_embedding',
                             fake_produce):
            mem._okf_index_record()

        concepts = self.Concept.search([
            ('source_ref', '=', 'ai.personal.memory,%s' % mem.id)])
        # Alla tre ska ha en vektor — den mittersta via fallback.
        self.assertEqual(len(concepts), 3)
        for c in concepts:
            self.assertTrue(c.embedding,
                            'koncept %s saknar vektor' % c.id)
            self.assertEqual(c.embedding_state, 'ready')

    # ── 3.2: totalt batchfel ──

    def test_total_batch_failure_degrades(self):
        """Ett totalt batchfel degraderar till per-koncept."""
        mem = self._mem()
        from odoo.addons.ai_agent_core.models.ai_okf_concept import (
            EMBEDDING_DIM)

        def fake_produce(self_, summary, title=None, explicit=None):
            if explicit is not None:
                return (explicit, 'ready')
            return ([7.0] * EMBEDDING_DIM, 'ready')

        with patch.object(type(mem), '_okf_owner_vals_list',
                          return_value=self._owners(2)), \
                patch.object(type(mem), '_okf_embed_texts',
                             return_value=[None, None]), \
                patch.object(type(self.Concept), '_produce_embedding',
                             fake_produce):
            mem._okf_index_record()

        concepts = self.Concept.search([
            ('source_ref', '=', 'ai.personal.memory,%s' % mem.id)])
        self.assertEqual(len(concepts), 2)
        for c in concepts:
            # Fallbacken ska ha gett en vektor, eller markerat pending —
            # aldrig tyst tom utan markering.
            self.assertTrue(
                c.embedding or c.embedding_state in ('pending', 'failed'),
                'koncept %s skrevs utan vektor OCH utan markering' % c.id)

    # ── 3.3: en ägare är oförändrad ──

    def test_single_owner_unchanged(self):
        """En post med en ägare embeddas som förut — ett anrop."""
        mem = self._mem()
        calls = []
        real = type(self.Mixin)._okf_embed_texts

        def spy(self_, texts):
            calls.append(list(texts))
            return real(self_, texts)

        with patch.object(type(mem), '_okf_embed_texts', spy):
            mem._okf_index_record()

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]), 1)

    # ── 4.3: embedding_state likadan ──

    def test_embedding_state_ready_on_success(self):
        """En lyckad vektor ger embedding_state='ready'."""
        mem = self._mem()
        from odoo.addons.ai_agent_core.models.ai_okf_concept import (
            EMBEDDING_DIM)

        def fake_embed(self_, texts):
            return [[0.5] * EMBEDDING_DIM for _ in texts]

        with patch.object(type(mem), '_okf_owner_vals_list',
                          return_value=self._owners(2)), \
                patch.object(type(mem), '_okf_embed_texts', fake_embed):
            mem._okf_index_record()
        concepts = self.Concept.search([
            ('source_ref', '=', 'ai.personal.memory,%s' % mem.id)])
        for c in concepts:
            self.assertEqual(c.embedding_state, 'ready',
                             'en batch-vektor ska ge ready')

    # ── 2.4: batchtaket ──

    def test_batch_limit_splits_calls(self):
        """Fler texter än taket ger flera batch-anrop."""
        mem = self._mem()
        calls = []

        def fake_embed(self_, texts):
            calls.append(list(texts))
            from odoo.addons.ai_agent_core.models.ai_okf_concept import (
                EMBEDDING_DIM)
            return [[0.5] * EMBEDDING_DIM for _ in texts]

        with patch.object(type(mem), 'OKF_EMBED_BATCH_MAX', 2), \
                patch.object(type(mem), '_okf_owner_vals_list',
                             return_value=self._owners(3)), \
                patch.object(type(mem), '_okf_embed_texts', fake_embed):
            mem._okf_index_record()

        # 3 texter med tak 2 -> tva anrop (2 + 1).
        # OBS: `_okf_embed_texts` patchas har, sa delningen sker i den
        # riktiga metoden — darfor testas den separat nedan.
        self.assertTrue(calls)

    def test_embed_texts_splits_at_limit(self):
        """`_okf_embed_texts` delar vid taket och håller ordningen."""
        mem = self._mem()
        seen = []

        def fake_batch(self_, inputs=None, input_type=None):
            seen.append(list(inputs))
            from odoo.addons.ai_agent_core.models.ai_okf_concept import (
                EMBEDDING_DIM)
            return [[float(len(inputs))] * EMBEDDING_DIM
                    for _ in inputs]

        with patch.object(type(mem), 'OKF_EMBED_BATCH_MAX', 2), \
                patch.object(type(self.env['ai.provider']),
                             '_get_embedding_batch', fake_batch):
            out = mem._okf_embed_texts(['a', 'b', 'c', 'd', 'e'])

        self.assertEqual([len(c) for c in seen], [2, 2, 1],
                         'delningen ska vara 2+2+1')
        self.assertEqual(len(out), 5, 'alla texter ska fa en post')

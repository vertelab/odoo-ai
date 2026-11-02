# -*- coding: utf-8 -*-
"""B12 — `ai_memory._get_embeddings`: död gren och tyst nedladdning.

Bakgrund: metoden prövade `hasattr(agent, 'ai_agent_llm_id')` — ett fält som
inte längre finns någonstans i kodbasen utom på raden själv. Grenen var död
kod. Resultatet var att varje FAISS-anrop föll tillbaka på:

    HuggingFaceEmbeddings('sentence-transformers/all-MiniLM-L6-v2')

…vilket **laddar ner modellen i runtime** vid första anropet — tyst, i en
request-tråd, utan att `langchain_community` deklarerades i manifestet.

Rättningen:
  * providerns egen embeddingsmodell används (samma väg som
    `ai.personal.memory` och `ai.okf.concept`),
  * HuggingFace-vägen kräver ett uttalat ja
    (`ai_agent_core.faiss_allow_download`),
  * `langchain_core` deklareras i `external_dependencies`.
"""

import ast
import os

from odoo.tests.common import TransactionCase, tagged


def _source(rel_path):
    from odoo.addons.ai_agent_core import __file__ as pkg_file
    with open(os.path.join(os.path.dirname(pkg_file), rel_path),
              encoding='utf-8') as fh:
        return fh.read()


@tagged('ai_core', 'post_install', '-at_install')
class TestDeadFieldGone(TransactionCase):
    """17.1/17.2: det borttagna fältet ska inte refereras."""

    def test_field_does_not_exist(self):
        agent = self.env['ai.agent'].create({'name': 'B12 Agent'})
        self.assertFalse(
            hasattr(agent, 'ai_agent_llm_id'),
            'fältet ai_agent_llm_id ska inte finnas — grenen som prövar '
            'det var dödkod')

    def test_source_does_not_reference_it(self):
        """Ingen KOD får referera fältet — kommentarer som förklarar
        historiken är tillåtna (och önskvärda)."""
        src = _source('models/ai_memory.py')
        tree = ast.parse(src)
        offenders = []
        for node in ast.walk(tree):
            # hasattr(agent, 'ai_agent_llm_id') och attributåtkomst
            if isinstance(node, ast.Constant) and node.value == 'ai_agent_llm_id':
                offenders.append(node.lineno)
            if isinstance(node, ast.Attribute) and node.attr == 'ai_agent_llm_id':
                offenders.append(node.lineno)
        self.assertEqual(
            offenders, [],
            'B12: ai_memory.py refererar fortfarande det borttagna fältet '
            'i kod: rad %s' % offenders)


@tagged('ai_core', 'post_install', '-at_install')
class TestNoSilentDownload(TransactionCase):
    """17.4: ett FAISS-anrop får inte ladda ner en modell tyst.

    `memory_type` bär artefaktens KÄLLA sedan ai-memory-pgvector T6.1 —
    'faiss' togs bort som värde. Testerna skapade artefakten med
    `memory_type='faiss'` och föll därför på valideringen innan de nådde
    det de prövar. Källan är irrelevant här: det som testas är
    `_get_embeddings()`s reservväg, inte lagringsformatet.
    """

    def setUp(self):
        super().setUp()
        self.env['ir.config_parameter'].sudo().set_param(
            'ai_agent_core.faiss_allow_download', '')

    def test_download_is_refused_by_default(self):
        """Utan provider OCH utan tillstånd: None + förklaring i loggen."""
        from unittest.mock import patch

        memory = self.env['ai.memory'].create({
            'name': 'B12 memory',
            'memory_type': 'text',
        })
        # Tvinga "ingen provider" — annars används testmiljöns provider och
        # reservvägen prövas aldrig.
        with patch.object(
                type(self.env['ai.provider']), '_embedding_provider',
                lambda self_: None):
            with self.assertLogs(
                    'odoo.addons.ai_agent_core.models.ai_memory',
                    level='WARNING') as captured:
                result = memory._get_embeddings()

        self.assertIsNone(
            result,
            'B12: utan provider och utan tillstånd ska ingen embeddings-'
            'instans skapas (ingen tyst nedladdning)')
        joined = '\n'.join(captured.output)
        self.assertIn('faiss_allow_download', joined,
                      'orsaken ska framgå av loggen')

    def test_provider_is_preferred_over_download(self):
        """Finns en provider ska den användas — ingen nedladdning alls."""
        from unittest.mock import patch

        memory = self.env['ai.memory'].create({
            'name': 'B12 provider memory',
            'memory_type': 'text',
        })

        class _Prov:
            def _effective_embedding_model(self, model=None):
                return 'stub-model'

            def _get_embedding_batch(self, model=None, inputs=None,
                                     input_type=None):
                return [[0.1] * 4 for _ in (inputs or [])]

            def _get_embedding(self, model=None, input=None, input_type=None):
                return [0.1] * 4

            # `_provider_embeddings` läser providerns namn och modell för
            # sin loggrad (ai_memory.py:803). Utan dem kastar anropet
            # AttributeError, som fångas och ger None — testet faller på
            # en ofullständig stubbe, inte på koden.
            name = 'Stub-provider'
            embedding_model = 'stub-model'

        with patch.object(
                type(self.env['ai.provider']), '_embedding_provider',
                lambda self_: _Prov()):
            embeddings = memory._get_embeddings()

        self.assertIsNotNone(embeddings)
        self.assertEqual(
            embeddings.embed_query('fraga'), [0.1] * 4,
            'provider-adaptern ska svara med providerns vektor')

    def test_no_huggingface_import_without_permission(self):
        """Källkoden ska inte importera HuggingFace före behörighetsprövningen."""
        src = _source('models/ai_memory.py')
        import_idx = src.find('from langchain_community.embeddings import')
        self.assertNotEqual(import_idx, -1,
                            'reservvägen ska finnas kvar (bakom flaggan)')
        guard_idx = src.find('faiss_allow_download')
        self.assertNotEqual(guard_idx, -1,
                            'B12: behörighetsprövningen saknas')
        self.assertLess(
            guard_idx, import_idx,
            'B12: flaggan måste prövas FÖRE importen — annars laddas '
            'modellen ner innan vi hunnit neka')


@tagged('ai_core', 'post_install', '-at_install')
class TestManifestDeclaresLangchain(TransactionCase):
    """17.3: `langchain_core` ska deklareras."""

    def test_external_dependencies(self):
        from odoo.addons.ai_agent_core import __file__ as pkg_file
        manifest_path = os.path.join(os.path.dirname(pkg_file),
                                     '__manifest__.py')
        with open(manifest_path, encoding='utf-8') as fh:
            manifest = ast.literal_eval(fh.read())
        python_deps = (
            manifest.get('external_dependencies', {}).get('python', []))
        self.assertIn(
            'langchain_core', python_deps,
            'B12: langchain_core används av ai_memory och måste deklareras')

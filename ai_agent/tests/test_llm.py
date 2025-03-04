import importlib
from contextlib import contextmanager
from unittest.mock import patch, Mock

from odoo import Command, modules
from odoo.tests.common import new_test_user, TransactionCase, HttpCase

class TestLLM(TransactionCase):

    # @classmethod
    # def setUpClass(cls):
    #     super(TestLLM, cls).setUpClass()
    #     cls.self = cls.env[""]
    #     cls.product_tmpl_id = cls.env.ref("ai_agent.groq_product_template")
    #     cls.name = "Test LLM"
    #     cls.model_id = cls.env.ref("ai_agent.groq_product_attribute_value_llama-3-1-8b-instant")

    def setUp(self):
        super(TestLLM, self).setUp()
        self.my_model = self.env["ai.agent.llm"].create({"name": "Test LLM", "product_tmpl_id": self.env.ref("ai_agent.groq_product_template").id, "model_id": self.env.ref("ai_agent.groq_product_attribute_value_llama-3-1-8b-instant").id})

    # def setUp(self):
    #     super(TestLLM, self).setUp()
    #      self.my_model = self.env['ai.llm'].create({"name":})

    def test_get_llm(self):
        module = importlib.import_module(self.my_model.product_tmpl_id.llm_library)
        LLM = getattr(module, self.my_model.product_tmpl_id.llm_type)
        result = self.my_model.get_llm()
        self.assertEqual(isinstance(result,LLM), True, "The LLM from the setup should be of the same class as the one gotten from get_llm.")

    # def test_llm_invoke(self):

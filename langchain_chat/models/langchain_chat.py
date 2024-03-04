from odoo import models, fields, api, _
import openai, langchain_openai, logging, requests

_logger = logging.getLogger(__name__)

class LangchainChat(models.Model):
    _name = "langchain.chat"
    _description = "AI Chat"

    api_key = fields.Char(string="API Keey")
    prompt = fields.Text(string="Prompt")
    response = fields.Text(string="Answer")

    def get_client(self):
        api_key = self.env['langchain.chat.settings'].search([], limit=1).api_key
        client = openai.OpenAI(api_key=api_key)
        _logger.info(f"Client: {client}")
        return client

    def generate_response(self):
        # Anslut till OpenAI API
        client = self.get_client()

        # Hämta prompten
        prompt = self.prompt

        # Generera svar med LangChain och OpenAI GPT-3.5
        response = client.complete(
            engine="davinci",
            prompt=f"{prompt}",
            temperature=0.7,
            max_tokens=100,
        )

        # Spara svaret i fältet
        self.response = response["choices"][0]["text"]

        # Returnera svaret
        return response
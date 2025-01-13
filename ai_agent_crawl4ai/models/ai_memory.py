import asyncio
from crawl4ai import AsyncWebCrawler
from odoo import models, api, fields, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class AIMemory(models.Model):
    _inehrit = 'ai.memory'

    url = fields.Char(string='Url', trim=True, )
    ai_type = fields.Selection(selection_add=[('crawl4ai','Crawl4AI')], ondelete={'crawl4ai': 'cascade'})
 
    def run(self):
        if self.ai_type == 'crawl4ai':                
            self.last_run = fields.Datetime.now()
            async def run_crawler():
            # Create an instance of AsyncWebCrawler
                async with AsyncWebCrawler() as crawler:
                    # Run the crawler on a URL
                    result = await crawler.arun(url=self.url)
                    return result
            # Use asyncio.run() to execute the async function in a synchronous manner
            self.memory_markdown = asyncio.run(run_crawler())

class AIAgent(models.Model):
    _inherit = "ai.agent"

    ai_type = fields.Selection(selection_add=[('crawl4ai','Crawl4AI')], ondelete={'crawl4ai': 'cascade'})

class AIQuest(models.Model):
    _inherit = "ai.quest"

    ai_type = fields.Selection(selection_add=[('crawl4ai','Crawl4AI')], ondelete={'crawl4ai': 'cascade'})


class AISession(models.Model):
    _inherit = "ai.quest.session"

    ai_type = fields.Selection(selection_add=[('crawl4ai','Crawl4AI')], ondelete={'crawl4ai': 'cascade'})




from bs4 import BeautifulSoup
from crawl4ai import AsyncWebCrawler
from io import StringIO
from odoo import models, api, fields, _
from odoo import models, fields, api
from odoo.exceptions import UserError, AccessError, ValidationError
from scrapy.crawler import CrawlerProcess
from urllib.parse import urljoin, urlparse
import asyncio
import logging
import markdownify
import requests
import scrapy


_logger = logging.getLogger(__name__)

class WebsiteCrawler(scrapy.Spider):
    name = 'website_crawler'
    start_urls = []
    
    def __init__(self, *args, **kwargs):
        super(WebsiteCrawler, self).__init__(*args, **kwargs)
        self.memory = kwargs.get('memory')
        self.all_content = StringIO()
        start_urls=[memory.url]

    def parse(self, response):
        # Extract text content
        text_content = ' '.join(response.css('body ::text').getall())
        _logger.warning(text_content)
        # Append to all_content with URL as header
        self.all_content.write(f"\n\n### URL: {response.url}\n\n")
        self.all_content.write(text_content)

        # Follow links within the same domain
        for link in response.css('a::attr(href)').getall():
            yield response.follow(link, self.parse)

    def closed(self, reason):
        # Create a single attachment with all content when spider closes
        memory.memory_markdown = markdownify.markdownify(self.all_content.getvalue().encode('utf-8'))


class AIMemory(models.Model):
    _inherit = 'ai.memory'

    url = fields.Char(string='Url', trim=True, )
    ai_type = fields.Selection(selection_add=[('crawl4ai','Crawl4AI'),('spyder','Spyder'),('bs4','BS4')], ondelete={'crawl4ai': 'cascade','spyder': 'cascade','bs4': 'cascade'})
 
    def run(self):
        _logger.warning(f'start scraping {self.ai_type=} {self.url=}')
        if self.ai_type == 'crawl4ai':
            # sudo apt install libgstreamer-gl1.0-0 libgstreamer-plugins-base1.0-0 libflite1 libavif-dev libharfbuzz-icu0 libenchant-2-2 libsecret-1-0 libhyphen0 libmanette-0.2-0 libgles2
            # sudo su odoo
            # npx playwright install --with-deps
            # playwright install
            # npx playwright install-deps chromium
            self.last_run = fields.Datetime.now()
            async def run_crawler():
            # Create an instance of AsyncWebCrawler
                async with AsyncWebCrawler() as crawler:
                    # Run the crawler on a URL
                    result = await crawler.arun(url=self.url)
                    return result
            # Use asyncio.run() to execute the async function in a synchronous manner
            self.memory_markdown = asyncio.run(run_crawler())
        elif self.ai_type == 'spyder':
            _logger.warning(f'start scraping {self.url}')
            process = CrawlerProcess(settings={
                'USER_AGENT': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36'
            })
            process.crawl(WebsiteCrawler, memory=self)
            process.start()
        elif self.ai_type == 'bs4':
            all_pages = self.scrape_website(self.url)
            _logger.warning(f"scrape ended {len(all_pages)=} -----------------------------------------")
            self.memory_markdown = self.scrape_website(self.url)
        else:
            super(AIMemory,self).run()
    def scrape_website(self,website):
        self.ensure_one()
        global all_pages
        all_pages = ""
        def is_same_domain(url, domain):
            return urlparse(url).netloc == urlparse(domain).netloc

        def scrape_page(url, visited):
            global all_pages
            _logger.warning(f'scraping {url=} {visited=} {len(all_pages)=}')
            if url in visited:
                return
            visited.add(url)
            
            try:
                response = requests.get(url)
                soup = BeautifulSoup(response.content, 'html.parser')
                
                # Save page content as attachment
                content = soup.get_text()
                all_pages += markdownify.markdownify(content)
                
                # Follow links
                for link in soup.find_all('a', href=True):
                    next_url = urljoin(url, link['href'])
                    if is_same_domain(next_url, website):
                        scrape_page(next_url, visited)
            except Exception as e:
                _logger.error(f"Error scraping {url}: {str(e)}")
        visited_urls = set()
        scrape_page(website, visited_urls)
        return all_pages.encode('utf-8')

class AIAgent(models.Model):
    _inherit = "ai.agent"

    ai_type = fields.Selection(selection_add=[('crawl4ai','Crawl4AI'),('spyder','Spyder')], ondelete={'crawl4ai': 'cascade','spyder': 'cascade'})

class AIQuest(models.Model):
    _inherit = "ai.quest"

    ai_type = fields.Selection(selection_add=[('crawl4ai','Crawl4AI'),('spyder','Spyder')], ondelete={'crawl4ai': 'cascade','spyder': 'cascade'})


class AISession(models.Model):
    _inherit = "ai.quest.session"

    ai_type = fields.Selection(selection_add=[('crawl4ai','Crawl4AI'),('spyder','Spyder')], ondelete={'crawl4ai': 'cascade','spyder': 'cascade'})



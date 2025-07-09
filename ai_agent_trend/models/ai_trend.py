from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
from googleapiclient.discovery import build
from odoo import models, fields, api, _
from odoo.addons.ai_agent_pgvector.fields.fields import PgVector
from odoo.addons.ai_agent_pgvector.models.embedding_mixin import EmbeddingMixin
from odoo.exceptions import UserError, ValidationError
from youtube_transcript_api import YouTubeTranscriptApi
import asyncio
import logging

_logger = logging.getLogger(__name__)

class AITrend(models.Model):
    _name = 'ai.trend'
    _description = 'AI Trend'
    
    ai_memory_id = fields.Many2one(comodel_name='ai.memory',string="AI-Memory",help="")
    channel_or_author = fields.Char(string='Channel/Author', search=True)
    comments = fields.Integer(string='Comments')
    content = fields.Text(string='Transcript / Article Content',search=True)
    content_embedding = PgVector(dimension=768)
    date_published = fields.Datetime(string='Published At',search=True)
    description = fields.Text(string='Description',search=True)
    likes = fields.Integer(string='Likes')
    linkedin_post_id = fields.Char(string='LinkedIn Post ID')
    name = fields.Char(string='Title', required=True,search=True)
    platform = fields.Selection([('youtube', 'YouTube'),('linkedin', 'LinkedIn')], string='Platform', required=True)
    topic = fields.Char(string='Topic',search=True)
    trend_score = fields.Float(string='Trend Score')
    url = fields.Char(string='URL', required=True)
    video_id = fields.Char(string='YouTube Video ID')
    views = fields.Integer(string='Views')


    def run(self):
        self.ai_memory_id.run()


    def get_latest_most_viewed_videos(self,topic, region_code='SE', max_results=50,transcript_lang='sv'):
        def check_exist(video_id):
            return self.env['ai.trend'].search_count([('video_id','=',video_id)]) > 0
        
        
        youtube_api_key = self.env['ir.config_parameter'].sudo().get_param('ai_agent_trend.youtube_api_key',None)
        if not youtube_api_key:
            raise UserError('Missing Youtube API Key Keys are created at https://console.cloud.google.com')
        youtube = build('youtube', 'v3', developerKey=youtube_api_key)

        search_response = youtube.search().list(
            q=topic,
            type='video',
            part='id,snippet',
            order='date',  # Sortera på uppladdningsdatum, nyast först
            regionCode=region_code,
            maxResults=max_results
        ).execute()

        existing_video_ids = self.env['ai.trend'].search([('video_id', 'in', [item['id']['videoId'] for item in search_response['items']])
            ]).mapped('video_id')
        video_ids = [
            item['id']['videoId']
            for item in search_response['items']
            if item['id']['videoId'] not in existing_video_ids
        ]

        # Get statistics
        videos_response = youtube.videos().list(
            id=','.join(video_ids),
            part='snippet,statistics'
        ).execute()

        # Sort
        videos = []
        for item in videos_response['items']:
            video_id = item['id']
            videos.append({
                'title': item['snippet']['title'],
                'channel': item['snippet']['channelTitle'],
                'date_published': item['snippet']['publishedAt'],
                'videoId': video_id,
                'views': int(item['statistics'].get('viewCount', 0)),
                'url': f"https://www.youtube.com/watch?v={item['id']}"
            })
        videos = sorted(videos, key=lambda x: x['views'], reverse=True)[:10]
        if videos:
            for v in videos:
               try:
                    transcript = YouTubeTranscriptApi.get_transcript(v['videoId'], languages=[transcript_lang, 'en'])
                    transcript_text = " ".join([x['text'] for x in transcript])
                    v['content'] = transcript_text
               except Exception as e:
                    v['content'] = None
            self.env['ai.trend'].create(videos)

        return videos[:10]
        
    def fetch_linkedin_trending_posts(self,topic: str, max_posts: int = 10):
        async def async_crawl():
            search_url = f"https://www.linkedin.com/search/results/content/?keywords={topic}&origin=GLOBAL_SEARCH_HEADER"
            schema = {
                "name": "LinkedInPost",
                "baseSelector": "div.search-result__wrapper",
                "fields": [
                    {"name": "author", "selector": "span.feed-shared-actor__name", "type": "text"},
                    {"name": "content", "selector": "div.feed-shared-update-v2__description", "type": "text"},
                    {"name": "date_published", "selector": "span.feed-shared-actor__sub-description > span", "type": "text"}
                ]
            }
            extraction_strategy = JsonCssExtractionStrategy(schema)
            run_config = CrawlerRunConfig(
                extraction_strategy=extraction_strategy,
            )

            async with AsyncWebCrawler() as crawler:
                result = await crawler.arun(url=search_url, config=run_config)
                if not result.success:
                    raise Exception(f"Crawl failed: {result.error_message}")
                # Använd .results istället för .data
                print(dir(result))
                return result._results[:max_posts]

        return asyncio.run(async_crawl())

# Exempelanvändning:
# trending_posts = fetch_linkedin_trending_posts("affärssystem", max_posts=10)
# for post in trending_posts:
#     print(post)

class AIMemory(models.Model):
    _inherit = 'ai.memory'

    ai_trend_ids = fields.One2many(comodel_name="ai.trend", inverse_name="ai_memory_id")
    ai_trend_topics = fields.Char(string="Topics",help="Comaseparated list of topics")
    ai_trend_topics_nbr = fields.Integer(string="Topics",compute="_ai_trend_topics_nbr")
    memory_type = fields.Selection(selection_add=[("ai_trend", "AI Trend")],ondelete={'ai_trend': 'cascade'})

    @api.onchange('memory_type')
    def _onchange_memory_type_trend(self):
        if self.memory_type == 'ai_trend':
            self.vector_type = 'pg_vector'


    @api.depends('ai_trend_ids')
    def __ai_trend_topics_nbr(self):
        for m in self:
            m.ai_trend_topics_nbr = len(m.ai_trend_ids)

    def run(self):
        for m in self:
            if m.memory_type == 'ai_trend':
                 for topic in [t.strip() for t in (m.ai_trend_topics or '').split(',') if t.strip()]:
                    self.env['ai.trend'].fetch_youtube_trending_posts(topic)
                    self.env['ai.trend'].fetch_linkedin_trending_posts(topic)
            else:
                super(AIMemory,m).run()       

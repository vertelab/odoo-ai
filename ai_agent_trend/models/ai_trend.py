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
    _inherit = "ai.memory"
    _description = 'AI Trend'
    
    channel_or_author = fields.Char(string='Channel/Author')
    comments = fields.Integer(string='Comments')
    content = fields.Text(string='Transcript / Article Content')
    content_embedding = PgVector(dimension=768)
    date_published = fields.Datetime(string='Published At')
    description = fields.Text(string='Description')
    likes = fields.Integer(string='Likes')
    linkedin_post_id = fields.Char(string='LinkedIn Post ID')
    name = fields.Char(string='Title', required=True)
    platform = fields.Selection([('youtube', 'YouTube'),('linkedin', 'LinkedIn')], string='Platform', required=True)
    topic = fields.Char(string='Topic')
    trend_score = fields.Float(string='Trend Score')
    url = fields.Char(string='URL', required=True)
    video_id = fields.Char(string='YouTube Video ID')
    views = fields.Integer(string='Views')


    def run(self):
        self.ai_memory_id.run()


    def get_latest_most_viewed_videos(api_key, topic, region_code='SE', max_results=50,transcript_lang='sv'):
        youtube = build('youtube', 'v3', developerKey="AIzaSyC3ULi_Tu8xjbg-Sm0pNnKlfNPT3ql-JU4")

        # 1. Sök efter de senaste videorna inom topic
        search_response = youtube.search().list(
            q=topic,
            type='video',
            part='id,snippet',
            order='date',  # Sortera på uppladdningsdatum, nyast först
            regionCode=region_code,
            maxResults=max_results
        ).execute()

        video_ids = [item['id']['videoId'] for item in search_response['items']]

        # 2. Hämta statistik (bl.a. visningar) för dessa videor
        videos_response = youtube.videos().list(
            id=','.join(video_ids),
            part='snippet,statistics'
        ).execute()

        # 3. Sortera videor på visningar (mest sedda först)
        videos = []
        for item in videos_response['items']:
            video_id = item['id']
            videos.append({
                'title': item['snippet']['title'],
                'channel': item['snippet']['channelTitle'],
                'publishedAt': item['snippet']['publishedAt'],
                'videoId': video_id,
                'views': int(item['statistics'].get('viewCount', 0)),
                'url': f"https://www.youtube.com/watch?v={item['id']}"
            })
        # Sortera listan på antal visningar, fallande
        videos.sort(key=lambda x: x['views'], reverse=True)
        for v in videos[:5]:
           try:
                transcript = YouTubeTranscriptApi.get_transcript(v['videoId'], languages=[transcript_lang, 'en'])
                # Slå ihop texten till en sträng
                transcript_text = " ".join([x['text'] for x in transcript])
                v['transcript'] = transcript_text
           except Exception as e:
                v['transcript'] = None  # Ingen transkription tillgänglig


        return videos[:10]
        
    def fetch_linkedin_trending_posts(topic: str, max_posts: int = 10):
        async def async_crawl():
            search_url = f"https://www.linkedin.com/search/results/content/?keywords={topic}&origin=GLOBAL_SEARCH_HEADER"
            schema = {
                "name": "LinkedInPost",
                "baseSelector": "div.search-result__wrapper",
                "fields": [
                    {"name": "author", "selector": "span.feed-shared-actor__name", "type": "text"},
                    {"name": "content", "selector": "div.feed-shared-update-v2__description", "type": "text"},
                    {"name": "date", "selector": "span.feed-shared-actor__sub-description > span", "type": "text"}
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


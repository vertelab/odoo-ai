import logging
import re
import requests
from bs4 import BeautifulSoup
from langchain.tools import tool
from langchain_community.tools.youtube.search import YouTubeSearchTool
from pydantic import BaseModel, Field, ConfigDict
from odoo.addons.ai_agent.models.ai_quest import AgentState

_logger = logging.getLogger(__name__)

def fetch_title_from_url(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")
        return soup.title.string.strip()
    except Exception as e:
        _logger.warning(f"Failed to fetch title for {url}: {e}")
        return "Okänd titel"

class YouTubeSearchInputs(BaseModel):
    query: str = Field(description="Vad ska sökas på YouTube. Max 10 ord.")
    state: AgentState = Field(description="Agentens interna tillstånd")
    model_config = ConfigDict(arbitrary_types_allowed=True)

def youtube_search_tool(state):
    @tool("youtube_search", return_direct=False)
    def youtube_search(query: str) -> str:
        """
        Söker efter YouTube-videor och returnerar klickbara HTML-länkar (5 resultat).
        """
        yt_tool = YouTubeSearchTool()
        result = yt_tool.run(query)
        _logger.error(f"{result=}")

        urls = re.findall(r'https://www\.youtube\.com/watch\?v=[\w\-]+', result)
        results = []
        seen = set()

        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            title = fetch_title_from_url(url)
            html_link = f'<a href="{url}" target="_blank">{title}</a>'
            results.append(html_link)
            if len(results) == 5:
                break

        return "<br>".join(results) if results else "Inga resultat hittades."

    return youtube_search


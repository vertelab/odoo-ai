import logging
import requests
from langchain.tools import tool
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from odoo.addons.ai_agent.models.ai_quest import AgentState as State
_logger = logging.getLogger(__name__)

@tool("internet_search_DDGO", return_direct=False)
def internet_search_DDGO(query: str, state:State) -> str:
    """Searches the internet using DuckDuckGo."""
    _logger.warning(f"{state=}")
    results = list(DDGS().text(query, max_results=5))

    return results if results else "No results found."

@tool("process_content", return_direct=False)
def process_content(url: str) -> str:   
    """Processes content from a webpage."""

    from bs4 import BeautifulSoup
    import requests

    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'html.parser')
    return soup.get_text()

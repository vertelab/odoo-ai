import logging
import requests
import sys
from langchain.tools import tool
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from odoo.addons.ai_agent.models.ai_quest import AgentState

# Import things that are needed generically
from pydantic import BaseModel, Field, ConfigDict
from langchain.tools import BaseTool, StructuredTool, tool



##if VERSION >= '18.0'
if sys.version_info >= (3, 12):
    from typing import Optional
else:
    from typing_extensions import Optional
##else
from typing_extensions import Optional
##endif

_logger = logging.getLogger(__name__)





class DDGOInputs(BaseModel):
    """Inputs to the internet_search_DDGO tool."""

    query: str = Field(
        description="query to look up in Internet, should be 10 or less words"
    )
    state: AgentState = Field(
        description="Graph State"
    )
    model_config = ConfigDict(arbitrary_types_allowed=True)


def internet_search(state):
    @tool("internet_search_DDGO", return_direct=False)
    def internet_search_DDGO(query: str) -> str:
        """Searches the internet using DuckDuckGo."""

        results = list(DDGS().text(query, max_results=5))

        return results if results else "No results found."

    return internet_search_DDGO

@tool("process_content", return_direct=False)
def process_content(url: str) -> str:   
    """Processes content from a webpage."""

    from bs4 import BeautifulSoup
    import requests

    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'html.parser')
    return soup.get_text()

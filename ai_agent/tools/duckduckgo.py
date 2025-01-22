import logging
import requests
from langchain.tools import tool
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
<<<<<<< HEAD
from odoo.addons.ai_agent.models.ai_quest import AgentState
from typing import Annotated

# Import things that are needed generically
from pydantic import BaseModel, Field
from langchain.tools import BaseTool, StructuredTool, tool


=======
from odoo.addons.ai_agent.models.ai_quest import AgentState as State
>>>>>>> 11780eaa6029bba55feb67d200c4ebc61b72446d
_logger = logging.getLogger(__name__)


class DDGOInputs(BaseModel):
    """Inputs to the internet_search_DDGO tool."""

    query: str = Field(
        description="query to look up in Internet, should be 10 or less words"
    )
    state: AgentState = Field(
        description="Graph State"
    )


@tool("internet_search_DDGO", return_direct=False)
<<<<<<< HEAD
def internet_search_DDGO(args_schema=DDGOInputs) -> str:
# ~ def internet_search_DDGO(query: str, state: State) -> str:
    """Searches the internet using DuckDuckGo."""
   
    _logger.error(f"{state=} -----------------------------------------------------------------------------")
=======
def internet_search_DDGO(query: str, state:State) -> str:
    """Searches the internet using DuckDuckGo."""
    _logger.warning(f"{state=}")
>>>>>>> 11780eaa6029bba55feb67d200c4ebc61b72446d
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

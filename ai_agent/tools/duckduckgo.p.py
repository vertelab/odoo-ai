import logging
import requests
from langchain.tools import tool
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS
from odoo.addons.ai_agent.models.ai_quest import AgentState
from typing import Annotated

# Import things that are needed generically
from pydantic import BaseModel, Field, ConfigDict
from langchain.tools import BaseTool, StructuredTool, tool


##if VERSION >= '18.0'
from typing import Annotated, List, NotRequired, Sequence, TypedDict, Union
##else
from typing_extensions import NotRequired, TypedDict
from typing import Annotated, List, Sequence, Union, Optional
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


@tool("internet_search_DDGO", return_direct=False)
def internet_search_DDGO(query: str, state: Optional[AgentState] = None) -> str:
# ~ def internet_search_DDGO(args_schema=DDGOInputs) -> str:
# ~ def internet_search_DDGO(query: str, state: State) -> str:
    """Searches the internet using DuckDuckGo."""
   
    _logger.error(f"{state=} -----------------------------------------------------------------------------")
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

import logging
import re
import requests
from bs4 import BeautifulSoup
from langchain.tools import tool
from langchain_community.utilities import SerpAPIWrapper
from odoo.addons.ai_agent.models.ai_quest import AgentState

_logger = logging.getLogger(__name__)

def serp_search_tool(state):
    @tool("youtube_search", return_direct=False)
    def serp_search(query: str, geo_location_code: str="se", host_language: str="en", search_engine: str="google") -> str:
        """Searches the internet with query, the default values for geo_location_code is se, 
        the host_language is en and the search_engine is google."""
        
        params = {
            "engine": search_engine,
            "gl": geo_location_code,
            "hl": host_language,
        }
        
        search = SerpAPIWrapper(params=params)
        
        result = search.run(query)
        
        return result if result else "No results found."

    return serp_search


import logging
import requests
import sys
import json
from langchain.tools import tool
from bs4 import BeautifulSoup
from odoo.addons.ai_agent.models.ai_quest import AgentState

# Import things that are needed generically
from pydantic import BaseModel, Field, ConfigDict
from langchain.tools import BaseTool, StructuredTool, tool

_logger = logging.getLogger(__name__)

def find_tasks_tool(state):
    @tool("find_tasks", return_direct=False)
    def find_tasks(project_name: str) -> str:
        """This tool lets you serach your odoo instance for tasks in a given project. It requires that you give it the variable project_name witch is a string for it to work."""
        
        quest = state.get("quest")

        if quest:

            project_id = quest.env["project.project"].sudo().search([("name", "ilike", project_name)],limit=1)
            result=""

            _logger.debug(f"project_id project_id {project_id=}")
            
            _logger.debug(f"projectssss {quest.env["project.project"].sudo().search([])=}")
            


            if not project_id:
                return f"No project found called {project_name}"

            if not project_id.task_ids:
                return f"No tasks found for the project {project_name}"

            task_ids = project_id.task_ids[:100]

            json_str = []
            for task in project_id.task_ids:
                json_str.append({"name":task.name, "description": task.description, "status": task.stage_id.name})

            result = str(json_str)
            #result = json.dumps(json_str)

            return result if result else "No results found."

        return "Error please tell user"

    return find_tasks



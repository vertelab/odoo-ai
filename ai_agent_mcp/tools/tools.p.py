import logging
from langchain.tools import tool
from odoo.addons.ai_agent.models.ai_quest import AgentState

_logger = logging.getLogger(__name__)

def find_tasks_tool(state):
    @tool("find_tasks", return_direct=False)
    def find_tasks(project_name: str) -> str:
        """
        This tool lets you search your odoo instance for tasks in a given project.
        It requires that you give it the variable project_name witch is a string for it to work.
        """
        quest = state.get("quest")

        if quest:
            project_id = quest.env["project.project"].sudo().search([("name", "ilike", project_name)],limit=1)

            if not project_id:
                return f"No project found called {project_name}"

            if not project_id.task_ids:
                return f"No tasks found for the project {project_name}"

            json_str = []
            for task in project_id.task_ids:
                json_str.append({"name":task.name, "description": task.description, "status": task.stage_id.name})

            result = str(json_str)
            return result if result else "No results found."
        return "No quest was found, hence you can't make a call. Contact Administrator for help"
    return find_tasks

def get_tasks_description(state):
    @tool("get_task_details", return_direct=False)
    def get_task_details(task_name: str) -> str:
        """
        This tool lets you search your odoo instance for tasks in a given project.
        It requires that you give it the variable project_name witch is a string for it to work.
        """
        quest = state.get("quest")

        if quest:
            task_id = quest.env["project.task"].sudo().search([("name", "ilike", task_name)],limit=1)
            print("task_id", task_id)

            if not task_id:
                return f"Could not find task with the name {task_name}"

            return f"Name: {task_id.name} \\n Status: {task_id.stage_id.name} \\n Description: {task_id.description}"

        return "No quest was found, hence you can't make a call. Contact Administrator for help"
    return get_task_details


def update_tasks_stage(state):
    @tool("update_task_stage", return_direct=False)
    def update_task_stage(task_name: str, status: str) -> str:
        """
        This tool lets you search your odoo instance for tasks in a given project.
        It requires that you give it the variable project_name witch is a string for it to work.
        """
        quest = state.get("quest")

        if quest:
            task_id = quest.env["project.task"].sudo().search([("name", "ilike", task_name)],limit=1)

            if not task_id:
                return f"Could not find task with the name {task_name}"

            if stage := task_id.project_id.type_ids.filtered(lambda task_stage: task_stage.name.lower() == status.lower()):
                task_id.stage_id = stage.id
                return f"Task {task_id.name}'s stage has been updated successfully."

            return f"Could not find stage with the name {status} in {task_id.name} is not found"
        return "No quest was found, hence you can't make a call. Contact Administrator for help"
    return update_task_stage

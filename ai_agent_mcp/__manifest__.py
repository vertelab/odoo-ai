{
    "name": "AI Agent MCP",
    "summary": "Adds MCP Power to AI Agent",
    "version": "1.0",
    "category": "AI",
    "author": "Vertel AB",
    "license": "AGPL-3",
    "depends": ["fastapi", "ai_agent"],
    'external_dependencies': {
        'python': ['fastapi-mcp', 'fastmcp']
    },
    "data": [
        # 'data/fastapi_data.xml',
        'views/ai_quest_view.xml',
        'views/ai_tool_view.xml',
    ],
    'post_init_hook': 'post_init_hook',
    "installable": True,
}

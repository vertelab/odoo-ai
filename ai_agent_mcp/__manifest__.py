{
    "name": "AI Agent MCP",
    "summary": "Adds MCP Power to AI Agent",
    "version": "1.0",
    "category": "AI",
    "author": "Vertel AB",
    "license": "AGPL-3",
    "depends": ["fastapi", "ai_agent"],
    'external_dependencies': {
        'python': ['fastapi-mcp']
    },
    "data": [
        'data/fastapi_data.xml',
        'views/ai_quest_view.xml',
    ],
    "installable": True,
}

{
    'name': 'odoo-ai: Agent Trend Analysis',
    'version': '0.3',
    'summary': 'Agent for analysing trends and create material for blogs and LinkedIn-articles',
    'category': 'Productivity / Discuss',
    'description': """
        Agent for analysing trends and create material for blogs and LinkedIn-articles
        
        Features:
        * AI-powered trend analysis
        * Blog content generation  
        * LinkedIn article creation
        * Data visualization and insights
        
        For professional implementation, customization and support services,
        contact Vertel AB at https://vertel.se/contact
    """,
    'sequence': 10,
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_trend',
    'images': ['static/description/banner.png'],
    'license': 'AGPL-3', 
    'contributor': '',
    'maintainer': 'Vertel AB',
    'depends': [
        'ai_agent_pgvector',
        'ai_agent_core',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/ai_trend_views.xml', 
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'support': 'https://github.com/vertelab/odoo-ai/issues',
}

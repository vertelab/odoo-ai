{
    'name': 'odoo-ai: Agent Trend Analysis',
    # Version = <Odoo series> + module version. Bump the module version in ALL branches.
    # #if VERSION == "20.0"
    'version': '20.0.0.3',
    # #elif VERSION == "18.0"
    'version': '18.0.0.3',
    # #else
    'version': '20.0.0.3',
    # #endif
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
        contact Vertel Sverige AB at https://vertel.se/contact
    """,
    'sequence': 10,
    'author': 'Vertel Sverige AB',
    'website': 'https://vertel.se/apps/odoo-ai/ai_agent_trend',
    'images': ['static/description/banner.png'],
    'license': 'AGPL-3', 
    'contributor': '',
    'depends': [
        'ai_agent_pgvector',
    ],
    'data': [
        # #if VERSION >= "20.0"
        'security/ir.access.csv',
        # #else
        'security/ir.model.access.csv',
        # #endif
        'views/ai_trend_views.xml', 
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'auto_install': False,
    'application': False,
    'support': 'https://github.com/vertelab/odoo-ai/issues',
}

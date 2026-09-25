{
    'name': 'User Mail: IMAP Client',
    'version': '18.0.1.2.0',
    'summary': 'IMAP email client integrated in Odoo — with per-user poller.',
    'description': '''
IMAP Client
===========

    IMAP email client integrated in Odoo — with per-user poller.

    Features:

        - Web integration: Exposes HTTP endpoints for external systems.
        - Automation: Scheduled jobs: User Mail: IMAP Poll (hjälpredan).
        - Guided Wizards: Step-by-step dialogs for data entry.
        - UI Integration: Extends 1 view(s) in the Odoo interface.
        - Extends Odoo: Builds on user.mail.imap, user.mail.poll, user.mail.processed.
    ''',
    'category': 'Productivity',
    'author': 'Vertel AB',
    'website': 'https://vertel.se/apps/odoo-ai/user_mail_imap',
    'license': 'AGPL-3',
    'depends': ['user_mail_common', 'mail', 'web'],
    'external_dependencies': {
        'python': ['cryptography'],
    },
    'data': [
        'security/ir.model.access.csv',
        'data/cron.xml',
        'views/menus_views.xml',
        'wizards/imap_password_wizard.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'user_mail_imap/static/src/services/imap_store.esm.js',
            'user_mail_imap/static/src/js/mail_imap.esm.js',
            'user_mail_imap/static/src/xml/mail_imap.xml',
            'user_mail_imap/static/src/components/folder_tree/folder_tree.esm.js',
            'user_mail_imap/static/src/components/folder_tree/folder_tree.xml',
            'user_mail_imap/static/src/components/mail_list/mail_list.esm.js',
            'user_mail_imap/static/src/components/mail_list/mail_list.xml',
            'user_mail_imap/static/src/components/mail_detail/mail_detail.esm.js',
            'user_mail_imap/static/src/components/mail_detail/mail_detail.xml',
            'user_mail_imap/static/src/components/composer/composer.esm.js',
            'user_mail_imap/static/src/components/composer/composer.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}

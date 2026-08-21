from odoo import http
from odoo.http import request


class ImapTemplateController(http.Controller):

    @http.route('/imap/templates', type='json', auth='user')
    def templates(self):
        """List mail.templates available for the user."""
        Template = request.env['mail.template'].sudo()
        records = Template.search_read(
            [('user_id', '=', request.env.user.id)],
            ['id', 'name', 'subject', 'body_html', 'model_id'],
        )
        return {'templates': records}

    @http.route('/imap/template/render', type='json', auth='user')
    def template_render(self, template_id):
        """Render a mail.template and return subject + body."""
        template = request.env['mail.template'].sudo().browse(template_id)
        if not template.exists():
            return {'error': 'Template not found'}
        rendered = template._render_template(
            template.body_html,
            template.model_id.model,
            request.env.user.id,
        )
        return {
            'subject': template.subject or '',
            'body_html': rendered.get(template.lang or '') or rendered.get('') or '',
        }

    @http.route('/imap/partners', type='json', auth='user')
    def partners(self, query=''):
        """Search res.partner by email or name for recipient autocomplete."""
        Partner = request.env['res.partner'].sudo()
        domain = [('email', '!=', False)]
        if query:
            domain += ['|', ('email', 'ilike', query), ('name', 'ilike', query)]
        records = Partner.search_read(domain, ['id', 'name', 'email'], limit=20)
        return {'partners': records}

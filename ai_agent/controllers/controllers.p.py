from odoo import http
from odoo.http import request

class MyModuleController(http.Controller):

    @http.route('/ai_agent/download_code/<int:record_id>', type='http', auth='user')
    def download_code(self, record_id, **kwargs):
        record = request.env['ai.quest'].browse(record_id)
        zip_bytes = record.generate_simple_module()  # Din metod som skapar zip
        filename = 'my_module.zip'
        headers = [
            ('Content-Type', 'application/zip'),
            ('Content-Disposition', f'attachment; filename="{filename}"'),
        ]
        return request.make_response(zip_bytes, headers)

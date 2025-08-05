from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.safe_eval import safe_eval
from random import randint
import logging

_logger = logging.getLogger(__name__)





class ResMCP(models.Model):
    _name = 'res.mcp'
    _description = 'External MCP resources'
    _inherit = ["mail.thread", "mail.activity.mixin"]
    
    color = fields.Integer(default=lambda self: randint(1, 11))
    company_id = fields.Many2one(comodel_name='res.company',string="Company",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    cron_interval_number = fields.Integer()
    cron_interval_type = fields.Selection(selection=[("minutes", "Minutes"),("hours", "Hours"), ("days", "Days"), ("weeks","Weeks"), ("months", "Months")],default="days")
    cron_record_limit = fields.Integer()
    debug = fields.Boolean(string='Debug')
    description = fields.Text(string='Description')
    image_128 = fields.Image(string="Image", max_width=128, max_height=128)
    is_favorite = fields.Boolean()
    last_run = fields.Datetime(string='Last Run',)
    mcp_api_key = fields.Char(string='API-Key')
    name = fields.Char(string='MCP-server')
    sequence = fields.Integer(string='Sequence')
    status = fields.Selection(selection=[("draft", "Draft"), ("active", "Active"), ("done", "Done"), ("error", "Error")], default="draft")
    status_color = fields.Integer(compute="compute_status_color")
    url = fields.Char(string='Url', trim=True, )

    @api.depends("status")
    def compute_status_color(self):
        for record in self:
            record.status_color = 0
            if record.status == "draft":
                record.status_color = 3  # Orange
            elif record.status == "active":
                record.status_color = 10  # Green
            elif record.status == "done":
                record.status_color = 3  # Orange
            elif record.status == "error":
                record.status_color = 1  # Red



    # ------------------------------------------------------------
    # Model/CRUD
    # ------------------------------------------------------------

    def run(self):
        if self.debug:
            self.real_run()
        else:
            self.with_delay().real_run()

    def cron(self):
        self.env['ai.memory'].search(
            [('nbr_days','>',0),('last_run', '<', fields.Datetime.now() - relativedelta(days=self.nbr_days))]).run()


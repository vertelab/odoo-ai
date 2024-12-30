import uuid

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

_logger = logging.getLogger(__name__)

class AIQuestSession(models.Model):
    _name = 'ai.quest.session'
    _description = 'AI Quest Session'
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _rec_name = "session"


    
    # ~ analytic_account_id = fields.Many2one(comodel_name='analytic.account',string="",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    ai_agent_id = fields.Many2one(comodel_name="ai.agent")
    ai_agent_ids = fields.Many2many(comodel_name="ai.agent")
    ai_agent_llm_id = fields.Many2one(comodel_name="ai.agent.llm")
    ai_agent_llm_ids = fields.Many2many(comodel_name="ai.agent.llm")
    ai_quest_id = fields.Many2one(comodel_name="ai.quest")
    session_line_ids = fields.One2many(comodel_name="ai.quest.session.line", inverse_name="ai_quest_session_id")
    ai_type = fields.Selection(selection=[("default","Default")], default="default")
    color = fields.Integer()
    commercial_partner_id = fields.Many2one(comodel_name='res.partner',string="Partner", related="user_id.partner_id.commercial_partner_id", help="", store=True)
    db_name = fields.Char(string='Database Name', default=lambda self: self._get_db_name())
    db_uuid = fields.Char(string='Database UUID', default=lambda self: self._get_db_uuid())
    debug = fields.Boolean(string='Debug', help="Logs interesting data")
    enddate = fields.Datetime()
    session = fields.Char(default=lambda self: str(uuid.uuid4()))
    startdate = fields.Datetime(default=fields.Datetime.now())
    status = fields.Selection(selection=[("draft",_("Draft")),("active",_("Active")),("done",_("Done")),("error",_("Error"))], default="draft")
    time_difference_ms = fields.Integer(string='Time Difference (ms)', compute='_compute_time_difference')
    type_of_output = fields.Text()
    user_id = fields.Many2one(comodel_name='res.users',string="User",help="")
    @api.model
    def _get_db_uuid(self):
        return self.env['ir.config_parameter'].sudo().get_param('database.uuid')
    @api.model
    def _get_db_name(self):
        return self.env.cr.dbname

    @api.depends('startdate', 'enddate')
    def _compute_time_difference(self):
        for record in self:
            if record.startdate and record.enddate:
                start = fields.Datetime.from_string(record.startdate)
                end = fields.Datetime.from_string(record.enddate)
                time_delta = end - start
                record.time_difference_ms = int(time_delta.total_seconds() * 1000)
            else:
                record.time_difference_ms = 0

    def store_session_data(self,aimessage,agent=None):
        self.env['ai.quest.session.line'].new_line(self,aimessage,agent=agent, debug=self.debug)

    def log(self,obj,message):
        _logger.info(message)
        obj.message_post(body=message)
        self.message_post(body=message)

    @api.model
    def llm_init(self,llm,debug=False):
        session_ids =  self.env['ai.quest.session'].search([
                ('ai_agent_llm_id','=',llm.id),('status','=','active')], limit=1)
        if len(session_ids)>=1:
            session=session_ids[0]
            if session.debug:
                session.log(llm,f"[session] revisit {session.name=} {llm.name=}")
        else:
            session = self.env['ai.quest.session'].create({
                    'status': 'active',
                    'ai_agent_llm_id': llm.id,
                    'ai_agent_llm_ids': (6,0,[llm.id]),
                    'debug': debug,
                        })
            if session.debug:
                session.log(llm,f"[session] init {session.name=} {llm.name=}")
        return session    

    @api.model
    def agent_init(self,agent,debug=False):
        session_ids =  self.env['ai.quest.session'].search([
                ('ai_agent_id','=',agent.id),('status','=','active')], limit=1)
        if len(session_ids)>=1:
            session=session_ids[0]
            if self.debug:
                self.log(agent,f"")
            if session.debug:
                session.log(agent,f"[session] revisit {session.name=} {agent.name=}")
        else:
          _logger.warning(f"{agent=} {agent[0].ai_agent_llm_id=}")
          session = self.env['ai.quest.session'].create({
            'status': 'active',
            'ai_agent_id': agent.id,
            'ai_agent_ids': [(6, 0, [agent.id])],  # Corrected syntax
            'ai_agent_llm_id': agent.ai_agent_llm_id.id if agent.ai_agent_llm_id else None,
            'ai_agent_llm_ids': [(6, 0, [agent.ai_agent_llm_id.id])] if agent.ai_agent_llm_id else None,  # Corrected syntax
            'debug': debug,
            })
          if self.debug:
            session.log(agent,f"[session] init {session.name=} {agent.name=}")
        return session    
    @api.model
    def quest_init(self,quest,agents=[],debug=False):
        session_ids =  self.env['ai.quest.session'].search([
                ('ai_quest_id','=',quest.id),('status','=','active')], limit=1)
        if len(session_ids)>=1:
            session=session_ids[0]
            if session.debug:
                session.log(agent,f"[session] revisit {session.name=}")
        else:
            session = self.env['ai.quest.session'].create({
                'status': 'active',
                'ai_quest_id': quest.id,
                'ai_agent_id': agents[0].id if agents else None,
                'ai_agent_ids': (6,0,[agent.id for agent in agents]),
                'ai_agent_llm_id': agents[0].ai_agent_llm_id.id if agents else None,
                'ai_agent_llm_ids': (6,0,[agent.ai_agent_llm_id.id for agent in agents]),
                })
            if session.debug:
                session.log(agent,f"[session] init {session.name=}")
        return session    
            
            
                # ~ session = self.env['ai.quest.session'].create({'ai_quest_id': self.id,'status': 'active'})


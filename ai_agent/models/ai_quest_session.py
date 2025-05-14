import uuid

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import logging

from langchain_core.messages import AIMessage, HumanMessage, ChatMessage, SystemMessage, ToolMessage

_logger = logging.getLogger(__name__)


class AISessionObject(models.Model):
    _name = 'ai.session.object'
    _description = 'AI Session Object'
    _order = 'datetime desc'

    @api.depends('object_id')
    def _get_model(self):
        for record in self:
            if record.object_id:
                record.model_id = self.env['ir.model'].search([('model', '=', record.object_id._name)], limit=1)

    datetime = fields.Datetime(string='Date', default=fields.Datetime.now())
    ai_quest_id = fields.Many2one(comodel_name='ai.quest', related="ai_session_id.ai_quest_id", store=True)
    ai_session_id = fields.Many2one(comodel_name='ai.quest.session', string="", help="")
    model_id = fields.Many2one(comodel_name='ir.model', compute=_get_model, store=True)
    object_id = fields.Reference(string='Object',
                                 selection=lambda m: [(model.model, model.name) for model in
                                                      m.env['ir.model'].sudo().search([])],
                                 readonly=False, required=True)

    display_name = fields.Char(string="Object", compute='_compute_display_name')
    color = fields.Integer(related="ai_session_id.ai_quest_id.color")

    @api.depends('object_id')
    def _compute_display_name(self):
        for record in self:
            if record.model_id:
                record.display_name = f"[{record.model_id.name}] {record.object_id.display_name}"
            elif not record.model_id:
                record.display_name = f"{record.object_id.display_name}"
            else:
                record.display_name = False



class AIQuestSession(models.Model):
    _name = 'ai.quest.session'
    _description = 'AI Quest Session'
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = 'startdate desc'

    session = fields.Char(default=lambda self: str(uuid.uuid4()))
    name = fields.Char(default=lambda self: self.session)
    ai_agent_count = fields.Integer(compute='_compute_ai_agent_count')
    ai_agent_id = fields.Many2one(comodel_name="ai.agent")
    ai_agent_ids = fields.Many2many(comodel_name="ai.agent")
    ai_agent_llm_id = fields.Many2one(comodel_name="ai.agent.llm")
    ai_agent_llm_ids = fields.Many2many(comodel_name="ai.agent.llm")
    ai_llm_count = fields.Integer(compute='_compute_ai_llm_count')
    ai_memory_id = fields.Many2one(comodel_name="ai.memory")
    ai_quest_id = fields.Many2one(comodel_name="ai.quest")
    ai_tool_id = fields.Many2one(comodel_name="ai.tool")
    ai_type = fields.Selection(selection=[("default", "Default")], default="default")
    color = fields.Integer()
    commercial_partner_id = fields.Many2one(comodel_name='res.partner', string="Partner",
                                            related="user_id.partner_id.commercial_partner_id", help="", store=True)
    db_name = fields.Char(string='Database Name', default=lambda self: self._get_db_name())
    db_uuid = fields.Char(string='Database UUID', default=lambda self: self._get_db_uuid())
    debug = fields.Boolean(string='Debug', help="Logs interesting data")
    enddate = fields.Datetime()
    session_line_count = fields.Integer(compute='_compute_session_line_count')
    session_line_ids = fields.One2many(comodel_name="ai.quest.session.line", inverse_name="ai_quest_session_id")
    session_message_ids = fields.One2many(comodel_name="ai.quest.session.message", inverse_name="ai_quest_session_id")
    session_message_count = fields.Integer(compute='_compute_session_message_count')
    session_object_count = fields.Integer(compute='_compute_session_object_count')
    session_object_ids = fields.One2many(comodel_name="ai.session.object", inverse_name="ai_session_id")
    startdate = fields.Datetime(default=lambda self: fields.Datetime.now())
    status = fields.Selection(
        selection=[("draft", "Draft"), ("active", "Active"), ("done", "Done"), ("error", "Error")],
        default="draft")
    time_difference_ms = fields.Integer(string='Time Difference (ms)', compute='_compute_time_difference', store=True)
    type_of_output = fields.Text()
    user_id = fields.Many2one(comodel_name='res.users', string="User", help="")
    
    display_name = fields.Char(compute='_compute_display_name')
    company_id = fields.Many2one('res.company', required=True, related="ai_quest_id.company_id")
 
    @api.depends("name")
    def _compute_display_name(self):
        for record in self:
            record.display_name = record.name


    @api.depends('ai_agent_llm_ids')
    def _compute_ai_llm_count(self):
        for record in self:
            record.ai_llm_count = len(record.ai_agent_llm_ids)

    @api.depends('ai_agent_ids')
    def _compute_ai_agent_count(self):
        for record in self:
            record.ai_agent_count = len(record.ai_agent_ids)

    @api.depends('session_line_ids')
    def _compute_session_line_count(self):
        for record in self:
            record.session_line_count = sum([l.token_sys or 0 for l in record.session_line_ids])

    def action_get_llms(self):
        action = {
            'name': 'LLMs',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent.llm',
            'view_mode': 'kanban,tree,form,calendar',
            'target': 'current',
            'domain': [("id", 'in', self.ai_agent_llm_ids.ids)]
        }
        return action

    def action_get_agents(self):
        action = {
            'name': 'AI Agents',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [("id", 'in', self.ai_agent_ids.ids)]
        }
        return action

    def action_get_session_lines(self):
        action = {
            'name': 'Session Lines',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            'view_mode': 'tree,form,calendar,pivot',
            'target': 'current',
            'domain': [("ai_quest_session_id", '=', self.id)],
        }
        return action

    def action_get_session_messages(self):
        action = {
            'name': 'Messages',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.message',
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [("ai_quest_session_id", '=', self.id)],
        }
        return action
 
    def action_get_sessions(self):
        action = {
            'name': 'Sessions',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session',
            'view_mode': 'tree,form,calendar',
            'target': 'current',
            'domain': [("ai_quest_id", '=', self.id)]
        }
        return action

    def action_get_session_objects(self):
        action = {
            'name': 'Objetcs',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.session.object',
            'view_mode': 'tree,calendar',
            'target': 'current',
            'domain': [("ai_session_id", '=', self.id)]
        }
        return action

    @api.depends("session_object_ids")
    def _compute_session_object_count(self):
        for record in self:
            record.session_object_count = len(record.session_object_ids)

    @api.depends("session_message_ids")
    def _compute_session_message_count(self):
        for record in self:
            record.session_message_count = len(record.session_message_ids)

    def _message_set_main_attachment_id(self, attachment_ids):
        thread_ids = super(AIQuestSession, self)._message_set_main_attachment_id(attachment_ids)

        _logger.error(f"{self.session=}")

        if self.ai_quest_id and self.message_ids[0].message_type == "email":
            _logger.warning(f"{self.message_ids[0].body=}")

            self.ai_quest_id.mail(mail=self.message_ids[0], session=self)

        return thread_ids

    @api.model
    def _get_db_uuid(self):
        return self.env['ir.config_parameter'].sudo().get_param('database.uuid')

    @api.model
    def _get_db_name(self):
        return self.env.cr.dbname

    @api.depends('enddate')
    def _compute_time_difference(self):
        for record in self:
            if record.startdate and record.enddate:
                start = fields.Datetime.from_string(record.startdate)
                end = fields.Datetime.from_string(record.enddate)
                time_delta = end - start
                record.time_difference_ms = int(time_delta.total_seconds() * 1000)
            else:
                record.time_difference_ms = 0

    def store_session_data(self, result=None, objects=None, agent=None, memory=False, tool=False):
        if objects is None:
            objects = {}
        if result is not None:
            _logger.warning(f"store session data before loop {objects=} {result=}")
            self.enddate = fields.Datetime.now()
            self.status = 'done'
            for message in result:
                if isinstance(message, AIMessage):
                    self.env['ai.quest.session.line'].new_line(session=self, aimessage=message, agent=agent,
                                                               debug=self.debug, memory=memory, tool=tool)
            if objects:
                for rec in objects.get('records',[]):
                    self.env['ai.session.object'].create({
                        'ai_session_id': objects.get('ai_session_id').id,
                        'object_id': f"{rec._name}, {rec.id}",
                    })

                    # for o in objects:
                    #     self.env['ai.session.object'].create({
                    #         'ai_session_id': self.id,
                    #         'object_id': (o._name, o.id),
                    #     })
            
            self.env['ai.quest.session.message'].save_messages(self,result)

            self.status = 'done'
        else:
            self.status = 'error'
            if self.ai_quest_id.debug:
                self.message_post(body=f'Missing {result=}')

    def add_message(self, message, **kwarg):
        self.env['ai.quest.session.message'].add(self, message, **kwarg)
   
    def save_messages(self, message, **kwarg):
        self.message_post(body=f"save_message<br>{message=}<br><br>{type(message)=}<br>{isinstance(message, dict)=}<br>{isinstance(message, list)=}<br>{isinstance(message, AIMessage)=}")
        self.env['ai.quest.session.message'].save_messages(self,message,**kwarg)

 
    def log(self, obj, message):
        _logger.info(message)
        obj.message_post(body=message)
        self.message_post(body=message)

    @api.model
    def llm_init(self, llm, debug=False):
        llm.last_run = fields.Datetime.now()
        session_ids = self.env['ai.quest.session'].search([
            ('ai_agent_llm_id', '=', llm.id), ('status', '=', 'active')], limit=1)
        if len(session_ids) >= 1:
            session = session_ids[0]
            if session.debug:
                session.log(llm, f"[session] revisit {session.name=} {llm.name=}")
        else:
            session = self.env['ai.quest.session'].create({
                'status': 'active',
                'ai_agent_llm_id': llm.id if llm else None,
                'ai_agent_llm_ids': [(6, 0, [llm.id])] if llm else None,
                'debug': debug,
            })
            if session.debug:
                session.log(llm, f"[session] init {session.name=} {llm.name=}")
        return session

    @api.model
    def agent_init(self, agent, debug=False):
        agent.last_run = fields.Datetime.now()
        session_ids = self.env['ai.quest.session'].search([
            ('ai_agent_id', '=', agent.id), ('status', '=', 'active')], limit=1)
        if len(session_ids) >= 1:
            session = session_ids[0]
            if self.debug:
                self.log(agent, f"")
            if session.debug:
                session.log(agent, f"[session] revisit {session.name=} {agent.name=}")
        else:
            _logger.warning(f"{agent=} {agent[0].ai_agent_llm_id=}")
            session = self.env['ai.quest.session'].create({
                'status': 'active',
                'ai_agent_id': agent.id,
                'ai_agent_ids': [(6, 0, [agent.id])],  # Corrected syntax
                'ai_agent_llm_id': agent.ai_agent_llm_id.id if agent.ai_agent_llm_id else None,
                'ai_agent_llm_ids': [(6, 0, [agent.ai_agent_llm_id.id])] if agent.ai_agent_llm_id else None,
                # Corrected syntax
                'debug': debug,
            })
            if self.debug:
                session.log(agent, f"[session] init {session.name=} {agent.name=}")
        return session

    @api.model
    def quest_init(self, quest, debug=False):
        _logger.warning(f"{quest.id=}")
        quest.last_run = fields.Datetime.now()
        session_ids = self.env['ai.quest.session'].search([
            ('ai_quest_id', '=', quest.id), ('status', '=', 'active')], limit=1)
        if len(session_ids) >= 1:
            session = session_ids[0]
            if session.debug:
                session.log(agent, f"[session] revisit {session.name=}")
        else:
            agents = [a.ai_agent_id for a in quest.ai_agent_ids]
            r = {
                'status': 'active',
                'ai_quest_id': quest.id,
                'ai_agent_id': agents[0].id if agents else None,
                'ai_agent_llm_id': agents[0].ai_agent_llm_id.id if agents else None,
            }
            _logger.warning(f"{r=}")
            session = self.env['ai.quest.session'].create({
                'startdate': fields.Datetime.now(),
                'status': 'active',
                'ai_quest_id': quest.id,
                'ai_agent_id': agents[0].id if agents else None,
                'ai_agent_llm_id': agents[0].ai_agent_llm_id.id if agents else None,
            })
            if agents:
                session.write({
                    'ai_agent_ids': [(4, agent.id) for agent in agents if agent.id],
                    'ai_agent_llm_ids': [(4, agent.ai_agent_llm_id.id) for agent in agents if agent.ai_agent_llm_id],
                })
            if session.debug:
                session.log(agent, f"[session] init {session.name=}")
        return session

        # ~ session = self.env['ai.quest.session'].create({'ai_quest_id': self.id,'status': 'active'})

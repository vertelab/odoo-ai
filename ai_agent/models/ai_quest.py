from random import randint
import re
import unidecode

from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
from odoo.tools.safe_eval import safe_eval
import logging

_logger = logging.getLogger(__name__)

class AIQuestAgent(models.Model):
    _name = 'ai.quest.agent'
    _description = 'AI Quest AGent'
    
    ai_quest_id = fields.Many2one(comodel_name='ai.quest',string="",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    sequence = fields.Integer(string='Sequence')
    ai_agent_id = fields.Many2one(comodel_name='ai.agent',string="Agent",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegateagent_count = fields.Integer(compute="compute_agent_count")

# https://readmedium.com/langgraph-made-easy-a-beginners-guide-part-2-196e8b179119

DEFAULT_PYTHON_CODE = """# Available variables:
#  - env: Odoo Environment on which the action is triggered
#  - model: Odoo Model of the record on which the action is triggered; is a void recordset
#  - record: record on which the action is triggered; may be void
#  - records: recordset of all records on which the action is triggered in multi-mode; may be void
#  - time, datetime, dateutil, timezone: useful Python libraries
#  - float_compare: Odoo function to compare floats based on specific precisions
#  - log: log(message, level='info'): logging function to record debug information in ir.logging table
#  - UserError: Warning Exception to use with raise
#  - Command: x2Many commands namespace
# To return an action, assign: action = {...}\n\n\n\n"""

    # Python code


    
class AIQuest(models.Model):
    _name = 'ai.quest'
    _inherit = ["mail.thread", "mail.activity.mixin", "mail.alias.mixin"]
    _description = 'AI Quest'
   
   
    ai_agent_ids = fields.One2many(comodel_name='ai.quest.agent',inverse_name='ai_quest_id',string="",help="") # domain|context|auto_join|limit
    agent_count = fields.Integer(compute="compute_agent_count")
    ai_type = fields.Selection(selection=[("default","Default")], default="default")
    color = fields.Integer(default=lambda self: randint(1, 11))
    description = fields.Text()
    init_type = fields.Selection(selection=[('manual','Manual'),('mail','Mail'),('chat','Chat with User'),('channel','Chat with Channel'),('cron','Scheduled Action'),('server-action','Server Action')],string='Initiate',help="How the Quest is initialized", required=True, default='manual')
    is_favorite = fields.Boolean()
    last_run = fields.Datetime()
    llm_count = fields.Integer(compute="compute_llm_count")
    name = fields.Char(required=True)
    server_action_id = fields.Many2one('ir.actions.server', string='Server Action', help="Server action to be executed when this quest is initialized", ondelete="cascade")
    session_count = fields.Integer(compute="compute_session_count")
    session_ids = fields.One2many(comodel_name="ai.quest.session", inverse_name="ai_quest_id")
    session_line_count = fields.Integer(compute="compute_session_line_count")
    session_line_ids = fields.One2many(comodel_name="ai.quest.session", inverse_name="ai_quest_id")
    status = fields.Selection(selection=[("draft",_("Draft")),("active",_("Active")),("done",_("Done")),("error",_("Error"))], default="draft")

    alias_id = fields.Many2one(comodel_name='mail.alias', string='Alias', ondelete="restrict", required=True, help="The email address associated with this channel. New emails received will automatically create new leads assigned to the channel.")
    alias_user_id = fields.Many2one(comodel_name='res.users', related='alias_id.alias_user_id', readonly=False, inherited=True,)        # ~ domain=lambda self: [('groups_id', 'in', self.env.ref('sales_team.group_sale_salesman_all_leads').id)]
        
    cron_id = fields.Many2one(comodel_name='ir.cron',string="Scheduled Action",help="",ondelete="cascade") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    model_id= fields.Many2one(comodel_name='ir.model',string="Model",help="Bind this Quest to yhis model") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    
    code = fields.Text(string='Python Code', groups='base.group_system',
                       default=DEFAULT_PYTHON_CODE,
                       help="Write Python code that the action will execute. Some variables are "
                            "available for use; help about python expression is given in the help tab.")
    channel_id = fields.Many2one(comodel_name='mail.channel',string="Channel",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate
    chat_user_id = fields.Many2one(comodel_name='res.users',string="Chat User",help="") # domain|context|ondelete="'set null', 'restrict', 'cascade'"|auto_join|delegate

    def _get_alias_model_name(self):
        return 'ai.quest'

    @api.model
    def _get_alias_values(self):
        values = super(AIQuest, self)._get_alias_values()
        values['alias_model_id'] = self.env['ir.model']._get('ai.quest').id
        return values

    def start(self):
        pass

    @api.depends('session_line_ids')
    def compute_llm_count(self):
        for record in self:
            record.llm_count = len(set(record.session_line_ids.mapped('ai_agent_llm_id')))


    def action_get_llms(self):
        action = {
            'name': 'LLMs',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent.llm',
            'view_mode': 'kanban,tree,form,calendar',
            'target': 'current',
            'domain': [("session_line_ids.ai_quest_id", '=', self.id)]
        }
        return action
    def action_get_session_lines(self):
        action = {
            'name': 'Session Lines',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session.line',
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [("ai_quest_id", '=', self.id)]
        }
        return action
    def action_get_sessions(self):
        action = {
            'name': 'Sessions',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.quest.session',
            'view_mode': 'tree,form',
            'target': 'current',
            'domain': [("ai_quest_id", '=', self.id)]
        }
        return action
    def action_get_agents(self):
        action = {
            'name': 'AI Agents',
            'type': 'ir.actions.act_window',
            'res_model': 'ai.agent',
            'view_mode': 'kanban,tree,form',
            'target': 'current',
            'domain': [("session_line_ids.ai_quest_id", '=', self.id)]
        }
        return action

    @api.onchange('model_id')
    def _onchange_model_id(self):
        if self.init_type == 'server-action':
            if self.server_action_id:
                self.server_action_id.write(
                            {   'name':self.name, 
                                'model_id': self.model_id.id,
                                'binding_model_id': self.model_id.id,
                            })
        if self.init_type == 'cron':
            if self.cron_id:
                self.cron_id.write(
                            {   'name':self.name, 
                                'model_id': self.model_id.id,
                            })

    def _get_eid(self):
        eid = list(self.get_external_id().values())[0]
        _logger.warning(f"{eid=}")
        if not eid:
            eid = 'new.' + unidecode.unidecode(re.sub(r'[^a-zA-Z0-9åäö\s]', '', self.name.lower()).replace(' ', '_')) + f"_{int(''.join(filter(str.isdigit, str(self.id))))}"
            self.env['ir.model.data'].create({
                'name': eid,
                'module': 'new',
                'model': 'ai.quest',
                'res_id': self.id,
                })
        return eid


    @api.onchange('init_type')
    def _onchange_init_type(self):
        name = self.name
        # ~ if self.init_type != 'cron' and self.cron_id:
            # ~ self.cron_id.unlink()
        if self.init_type == 'cron':
            if not self.cron_id:
                self.cron_id = self.cron_id.create(
                            {   'name':self.name, 
                                'model_id': self.model_id.id if self.model_id else self.env.ref('base.model_res_partner').id,
                                'state': 'code',
                                'code': f"action = env.ref('{self._get_eid()}').cron()",
                            })
        if self.init_type != 'server-action' and self.server_action_id:
            self.server_action_id.unlink()

        if self.init_type == 'server-action':
            if not self.server_action_id:
                self.server_action_id = self.server_action_id.create(
                            {   'name':self.name, 
                                'model_id': self.model_id.id if self.model_id else self.env.ref('base.model_res_partner').id,
                                'state': 'code',
                                'code': f"action = env.ref('{self._get_eid()}').server_action(records)",
                            })
        if self.init_type != 'channel' and self.channel_id:
            self.channel_id.unlink()

        if self.init_type == 'channel':
            if not self.channel_id:
                self.channel_id = self.channel_id.create(
                            {   'name':self.name, 
                                'ai_quest_id':self.id, 
                            })
        if self.init_type != 'chat' and self.chat_user_id:
            self.chat_user_id.unlink()

        if self.init_type == 'chat':
            if not self.chat_user_id:
                self.chat_user_id= self.chat_user_id.create(
                            {   
                                'name':self.name, 
                                'login':self.name, 
                            })
        self.name = name





    def server_action(self,records):
        self.ensure_one()
        if self.init_type == 'server-action' and self.server_action_id:
            self.log_message(f'server-action {records}')
            
    def cron(self,records):
        self.ensure_one()
        if self.init_type == 'cron' and self.cron_id:
            self.log_message('cron')
            



    @api.depends("session_line_ids")
    def compute_session_line_count(self):
        for record in self:
            record.session_line_count = len(record.session_line_ids)

    @api.depends("session_ids")
    def compute_session_count(self):
        for record in self:
            record.session_count = len(record.session_ids)
    @api.depends("session_line_ids")
    def compute_agent_count(self):
        for record in self:
            record.agent_count = len(set(record.session_line_ids.mapped('ai_agent_id')))


    def log_message(self,body,is_error=False):
        if is_error:
            self.status = "error"
        self.last_run = fields.Datetime.now()
        self.message_post(body=f"{body} | {self.last_run}",message_type="notification")



    # ------------------------------------------------------------
    # ORM
    # ------------------------------------------------------------

    def write(self, vals):
        result = super(AIQuest, self).write(vals)
        if 'init_type' in vals and vals.get('init_type') == 'mail':
            for quest in self:
                alias_vals = quest._alias_get_creation_values()
                quest.write({
                    'alias_name': alias_vals.get('alias_name', quest.alias_name),
                    'alias_defaults': alias_vals.get('alias_defaults'),
                })
        return result

    def chat(self, message):
        # Implement your bot logic here
        # This is a simple example that just echoes the message
        agent = self.ai_agent_ids[0].ai_agent_id
        session = message.parent_id.ai_quest_session_id if message.parent_id and message.parent_id.ai_quest_session_id else \
                message.parent_id.ai_quest_session_id if message.ai_quest_session_id else \
                self.env['ai.quest.session'].quest_init(self,agents=[agent])
        res = self.with_context({'parameter': message,'session':session}).run()
        _logger.warning(f"{res=}")
        # ~ _logger.warning(f"{agent=}")
        # ~ return agent.prompt_agent(message.body,session=session)
        # ~ return f"Bot received: {message.body}"

    # ------------------------------------------------------------
    # MESSAGING
    # ------------------------------------------------------------

    def _alias_get_creation_values(self):
        values = super(AIQuest, self)._alias_get_creation_values()
        values['alias_model_id'] = self.env['ir.model']._get('ai.quest.session').id
        if self.id:
            values['alias_defaults'] = defaults = {}
            defaults['ai_quest_id'] = self.id
        return values


    def _get_eval_context(self, action=None):
        """ Prepare the context used when evaluating python code, like the
        python formulas or code server actions.

        :param action: the current server action
        :type action: browse record
        :returns: dict -- evaluation context given to (safe_)safe_eval """
        def log(message, level="info"):
            with self.pool.cursor() as cr:
                cr.execute("""
                    INSERT INTO ir_logging(create_date, create_uid, type, dbname, name, level, message, path, line, func)
                    VALUES (NOW() at time zone 'UTC', %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (self.env.uid, 'server', self._cr.dbname, __name__, level, message, "action", action.id, action.name))

        eval_context = {}
        # ~ eval_context = super(AIQuest, self)._get_eval_context(action=action)
        model_name = action.model_id.sudo().model
        model = self.env[model_name]
        record = None
        records = None
        if self._context.get('active_model') == model_name and self._context.get('active_id'):
            record = model.browse(self._context['active_id'])
        if self._context.get('active_model') == model_name and self._context.get('active_ids'):
            records = model.browse(self._context['active_ids'])
        if self._context.get('onchange_self'):
            record = self._context['onchange_self']
        eval_context.update({
            # orm
            'env': self.env,
            'model': model,
            # Exceptions
            'Warning': Warning,
            'UserError': UserError,
            # record
            'record': record,
            'records': records,
            # helpers
            'log': log,
            'parameter': self.env.context.get('parameter', None)
        })
        return eval_context

    def _get_runner(self):
        multi = True
        t = self.env.registry[self._name]
        fn = getattr(t, f'_run_action_{self.state}_multi', None)\
          or getattr(t, f'run_action_{self.state}_multi', None)
        if not fn:
            multi = False
            fn = getattr(t, f'_run_action_{self.state}', None)\
              or getattr(t, f'run_action_{self.state}', None)
        if fn and fn.__name__.startswith('run_action_'):
            fn = functools.partial(fn, self)
        return fn, multi


    def run(self):
        """ Runs the server action. For each server action, the
        :samp:`_run_action_{TYPE}[_multi]` method is called. This allows easy
        overriding of the server actions.

        The ``_multi`` suffix means the runner can operate on multiple records,
        otherwise if there are multiple records the runner will be called once
        for each.

        The call context should contain the following keys:

        active_id
            id of the current object (single mode)
        active_model
            current model that should equal the action's model
        active_ids (optional)
           ids of the current records (mass mode). If ``active_ids`` and
           ``active_id`` are present, ``active_ids`` is given precedence.
        :return: an ``action_id`` to be executed, or ``False`` is finished
                 correctly without return action
        """
        res = False
        for action in self.sudo():
            #TODO add security on ai.quest
            # ~ action_groups = action.groups_id
            # ~ if action_groups:
                # ~ if not (action_groups & self.env.user.groups_id):
                    # ~ raise AccessError(_("You don't have enough access rights to run this action."))
            # ~ else:
                # ~ try:
                    # ~ self.env[action.model_name].check_access_rights("write")
                # ~ except AccessError:
                    # ~ _logger.warning("Forbidden server action %r executed while the user %s does not have access to %s.",
                        # ~ action.name, self.env.user.login, action.model_name,
                    # ~ )
                    # ~ raise

            eval_context = self._get_eval_context(action)
            records = eval_context.get('record') or eval_context['model']
            records |= eval_context.get('records') or eval_context['model']
            if records:
                try:
                    records.check_access_rule('write')
                except AccessError:
                    _logger.warning("Forbidden server action %r executed while the user %s does not have access to %s.",
                        action.name, self.env.user.login, records,
                    )
                    raise

            def _run_action_code_multi(self, eval_context):
                safe_eval(self.code.strip(), eval_context, mode="exec", nocopy=True, filename=str(self))  # nocopy allows to return 'action'
            return eval_context.get('action')


            runner, multi = "_run_action_code_multi", True
            if runner and multi:
                # call the multi method
                run_self = action.with_context(eval_context['env'].context)
                res = runner(run_self, eval_context=eval_context)
                raise UserError(f"{res=} {eval_context=}")
            elif runner:
                active_id = self._context.get('active_id')
                if not active_id and self._context.get('onchange_self'):
                    active_id = self._context['onchange_self']._origin.id
                    if not active_id:  # onchange on new record
                        res = runner(action, eval_context=eval_context)
                active_ids = self._context.get('active_ids', [active_id] if active_id else [])
                for active_id in active_ids:
                    # run context dedicated to a particular active_id
                    run_self = action.with_context(active_ids=[active_id], active_id=active_id)
                    eval_context["env"].context = run_self._context
                    res = runner(run_self, eval_context=eval_context)
                raise UserError(f"{res=} {eval_context=}")
            else:
                _logger.warning(
                    "Found no way to execute server action %r of type %r, ignoring it. "
                    "Verify that the type is correct or add a method called "
                    "`_run_action_<type>` or `_run_action_<type>_multi`.",
                    action.name, action.state
                )
        return res or False

class MailMessage(models.Model):
    _inherit = 'mail.message'
    
    ai_quest_session_id = fields.Many2one(comodel_name='ai.quest.session',string="Session",help="") 



class MailChannel(models.Model):
    _inherit = 'mail.channel'
    
    ai_quest_id = fields.Many2one(comodel_name='ai.quest',string="Quest",help="") 
    ai_quest_session_id = fields.Many2one(comodel_name='ai.quest.session',string="Session",help="") 

    @api.returns('mail.message', lambda value: value.id)
    def message_post(self, **kwargs):
        message = super(MailChannel, self).message_post(**kwargs)
        
        # Check if the message is from a user (not the bot itself)
        _logger.warning(f"{message.author_id=} {message.parent_id=} {self.ai_quest_id=}")
        if message.author_id != self.env.ref('base.partner_root'):
            if self.ai_quest_id:
                bot_response = self.ai_quest_id.chat(message)
                if bot_response:
                    self.with_user(self.env.ref('base.user_root')).message_post(
                        body=bot_response,
                        message_type='comment',
                        subtype_xmlid='mail.mt_comment',
                    )
        return message

from langchain_core.messages import AIMessage, HumanMessage, ChatMessage, SystemMessage, ToolMessage
from odoo import models, fields, api, _
from odoo.exceptions import UserError, AccessError, ValidationError
import inspect
import logging
import time
import traceback
from odoo.addons.ai_agent.models.ai_quest import AgentState 
import markdown


_logger = logging.getLogger(__name__)

class AIQuestSessionMessage(models.Model):
    _name = 'ai.quest.session.message'
    _description = 'AI Quest Session Message'
    _order = 'sequence asc'

    sequence = fields.Integer()
    ai_quest_session_id = fields.Many2one(comodel_name="ai.quest.session")
    message_type = fields.Char(string='Message Type', size=64 )
    message_content = fields.Text()
    message_raw = fields.Text()
    message = fields.Text()
    message_html = fields.Html()
    
    traceback = fields.Text()
    date = fields.Datetime(default=lambda self: fields.Datetime.now())
    time_difference_ms = fields.Float(string='Time Difference (ms)', compute='_compute_time_difference', store=True)


    @api.model
    def add(self, session, message, **kwarg):
        if kwarg.get('mermaid',False):
            return None
            
        def format_dict(kw) -> str:          
            lines = []
            for attr,data in kw.items():
                # ~ if attr == "message":
                    # ~ lines.append(f"{data}")
                    # ~ lines.append("")
                    # ~ lines.append("-" * 80)
                    # ~ continue
                if attr == "state":
                    lines.append(f"**STATE**")
                    lines.append("")
                    for key,val in data.items():
                        lines.append(f"*{key}*")
                       
                        if isinstance(val,list):

                            
                            
                            
                            
                            for value in val:
                                lines.append(f"*Type:* {type(value).__name__}")
                                lines.append("")
                                lines.extend(str(value).replace('\\n','\n').split('\n'))
                                lines.append("")
                                lines.append("-" * 80)
                                continue
                                
                                
                                
                                if isinstance(value, (AIMessage, HumanMessage, ChatMessage, SystemMessage, ToolMessage)):
                                    lines.append(f"*Type:* {type(value).__name__}")
                                    lines.append("")
                                    try:
                                        # Pydantic v2: model_dump()
                                        attrs_data = value.model_dump()
                                    except AttributeError:
                                        try:
                                            # Pydantic v1 fallback: dict()
                                            attrs_data = value.dict()
                                        except:
                                            attrs_data = {}
                                    for attrs, vals in attrs_data.items():
                                        if attrs == 'content':
                                            lines.append("**Content**")
                                            clean_content = str(vals).replace('\\n', '\n').strip()
                                            lines.extend(clean_content.split('\n'))
                                            # ~ lines.extend(str(vals).replace('\\n', '\n').strip().split('\n'))

                                        # ~ elif attrs != 'content' and vals:  # Skippa content (redan visad)
                                            # ~ lines.append(f"**{attrs.replace('_', ' ').title()}:** {str(vals)[:100]}")
                                                            
                                        else:
                                            lines.append(f"**{attrs}**")
                                            lines.extend(str(vals).replace('\\n', '\n').strip().split('\n'))
                                        lines.append("")
                                        lines.append("-" * 80)
                        else:
                            lines.append(f"**{key}**")
                            lines.append(f"{val}")
                            lines.append("")
                            lines.append("-" * 80)
                    lines.append("")
                    lines.append("=" * 80)
                lines.append(f"{attr}: {data}")
                lines.append("")
                lines.append("#" * 80)
            return '\n'.join(lines)

        kwarg['message'] = message
            
        start = self.env['ai.quest.session.message'].search([('ai_quest_session_id',"=",session.id)],order="sequence desc",limit=1)
        start = start.sequence if start else 0
        session_message_id = self.env['ai.quest.session.message'].create({
                "sequence": start + 1,
                "ai_quest_session_id": session.id,
                "message_type": 'add',
                "message_content": message[:100],
                "message": format_dict(kwarg),
                # ~ "message_html": markdown.markdown(format_dict(kwarg).replace('\n','<br/>')),
                "message_html": markdown.markdown(format_dict(kwarg)),
                "message_raw": f"{kwarg}",
                "traceback": f"add\n{''.join(traceback.format_stack(limit=15))}",
            })
        return session_message_id

    
    @api.model
    def save_messages(self, session, messages,**kwarg):
        _logger.info(f"Session Save Messages {session=} {messages=}")
        if kwarg.get('mermaid',False):
            return None
            
        def format_dict(value) -> str:
            
            if isinstance(value, (AIMessage, HumanMessage, ChatMessage, SystemMessage, ToolMessage)):
                lines = []
                lines.append(f"**Type:** {type(value).__name__}")
                lines.append("")
                
                # Content (primärt)
                content = getattr(value, 'content', 'No content') or ''
                if content:
                    clean_content = str(content).replace('\\n', '\n').strip()
                    lines.extend(clean_content.split('\n'))
                    lines.append("")
                    lines.append("-" * 80)
                
                # FIX: Pydantic v2 - model_dump() istället för Dict
                try:
                    # Pydantic v2: model_dump()
                    attrs_data = value.model_dump()
                except AttributeError:
                    try:
                        # Pydantic v1 fallback: dict()
                        attrs_data = value.dict()
                    except:
                        # Ultimat fallback
                        attrs_data = {}
                
                # Iterera över attribut SAFELY
                for attr, val in attrs_data.items():
                    if attr != 'content' and val:  # Skippa content (redan visad)
                        lines.append(f"**{attr.replace('_', ' ').title()}:** {str(val)[:100]}")
                        lines.append("")
                        lines.append("-" * 80)
                
                return '\n'.join(lines)
            else:
                return str(value)

            

        start = self.env['ai.quest.session.message'].search([('ai_quest_session_id',"=",session.id)],order="sequence desc",limit=1)
        start = start.sequence + 1 if start else 1
        frame = inspect.currentframe().f_back
        
        # ~ raise UserError(f"{type(messages)} {type(messages[0])}{type(messages[1])}{type(messages[2])}")
        # ~ breakpoint()
        for seq, message in enumerate(messages):
            self.env['ai.quest.session.message'].create({
                "sequence": seq + start,
                "ai_quest_session_id": session.id,
                "message_type": type(message).__name__,
                "message_content": str(message)[:100],
                "message_html": f"{format_dict(message) if type(message) != 'str' else message}",
                "message": f"{format_dict(message) if type(message) != 'str' else message}",
                "message_raw": f"{message}",
                # ~ "prompt": kwarg.get('prompt',f"{traceback.format_exc()}"),
                #"traceback": f"save_message ({len(messages)})\n{''.join(traceback.format_stack(limit=15))}",
            })
            
    @api.depends('date')
    def _compute_time_difference(self):
        for record in self:
            record.time_difference_ms = int(time.time() * 1000) - record.ai_quest_session_id.starttime

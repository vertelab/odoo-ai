# -*- coding: utf-8 -*-
"""
Custom Tools — user-defined tools via ai.tool (TOOL-002).

ai.tool lets users define custom tools with Python code.
Tools are evaluated in a sandboxed context for safety.
"""

import json
import logging

from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo import SUPERUSER_ID

_logger = logging.getLogger(__name__)

RISK_LEVELS = [
    ('safe', 'Safe'),
    ('read_only', 'Read Only'),
    ('write', 'Write'),
    ('destructive', 'Destructive'),
    ('execute', 'Execute Code'),
]

EXECUTOR_SELECTION = [
    ('local', 'Local (AgentLoop)'),
    ('nats', 'NATS (Pi Agent)'),
]


class AITool(models.Model):
    """A user-defined tool that agents can call."""
    _name = 'ai.tool'
    _description = 'AI Tool'
    _order = 'name asc'

    name = fields.Char('Tool Name', required=True)
    active = fields.Boolean(default=True)

    # Builtin-verktyg (explicit-agent-tools): när satt är posten den synliga
    # representationen av ett inbyggt verktyg från core/tools.py
    # (builtin_tools()). Konvertering till runtime-verktyg hämtar den riktiga
    # Python-handlern — ingen sandbox-kod. Sätts idempotent av
    # _ensure_builtin_tool_records() vid varje moduluppdatering.
    builtin_name = fields.Char(
        'Builtin Name', readonly=True,
        help='Namnet på det inbyggda verktyget (core/tools.py) som denna post '
             'representerar. Tomt för custom-verktyg med egen kod. När satt '
             'används den riktiga handlern vid körning.',
    )
    description = fields.Text(
        'Description', required=True,
        help=('AI-beskrivning — det kontrakt LLM:en läser vid verktygsval. '
              'Använd mallen: syfte / när / när inte (peka på rätt verktyg) / '
              'exempel / output / guardrail. Guardrails är informativa här — '
              'enforcement sker via risk_level + PermissionEngine, aldrig '
              'via text.'),
    )
    code = fields.Text(
        'Code', required=False,  # Not required for NATS-executor tools
        help=(
            'Python code for this tool. Must define an async function '
            'named "execute" that takes keyword arguments and returns a string. '
            'Not needed for NATS-executor tools (executor="nats").'
        ),
    )
    parameters = fields.Text(
        'Parameters (JSON Schema)',
        default='{"type": "object", "properties": {}, "required": []}',
        help='JSON Schema for tool parameters. Defines what the LLM can pass.',
    )
    risk_level = fields.Selection(RISK_LEVELS, default='read_only', required=True)

    # Executor routing (tool-executor-nats)
    executor = fields.Selection(
        EXECUTOR_SELECTION, default='local', required=True,
        help='Where this tool executes. Local = in AgentLoop. NATS = via Pi-agent.',
    )
    capabilities = fields.Text(
        'Capabilities (JSON list)',
        default='[]',
        help='What capabilities this tool requires. E.g. ["browser"], ["infra"]. '
             'For future capability-based skill filtering.',
    )

    # Access-grupper (ai-tool-access-capabilities): vilka Odoo-grupper får
    # använda verktyget. Tom = obegränsat för alla som når coworkern — HITL
    # via risk_level gäller oavsett. Moduler skapar nya res.groups i XML.
    group_ids = fields.Many2many(
        'res.groups', 'ai_tool_res_group_rel',
        'tool_id', 'group_id', string='Access Groups',
        help='Odoo-grupper (res.groups) som får använda detta verktyg. '
             'Tom = obegränsat; HITL via risk_level gäller ändå. '
             'Moduler skapar nya grupper i XML vid behov.',
    )

    # NATS executor config (when executor="nats")
    nats_subject = fields.Char(
        'NATS Subject', default='pi.task.do',
        help='NATS subject for request-reply when executor="nats".',
    )
    nats_skills = fields.Char(
        'Pi-Agent Skills',
        help='Comma-separated Pi-agent skill names required for this tool. '
             'E.g. "agent-browser", "agent-browser, caddy-routing".',
    )
    nats_timeout = fields.Integer(
        'NATS Timeout (s)', default=30,
        help='Max seconds to wait for Pi-agent reply.',
    )

    # Systemtoken-kostnad per anrop (budget-hard-cap D6)
    sys_token_cost = fields.Integer(
        'Systemtoken-kostnad per anrop', default=500,
        help='Fast minimikostnad i systemtokens per tool-anrop. Läggs '
             'direkt på tool-raden (token_sys). Global per tool.',
    )

    # Relations
    agent_ids = fields.Many2many(
        'ai.agent', 'ai_agent_tool_custom_rel',
        'tool_id', 'agent_id', string='Used by Agents',
    )
    coworker_ids = fields.Many2many(
        'ai.coworker', 'ai_coworker_tool_custom_rel',
        'tool_id', 'coworker_id', string='Used by Quests',
    )

    # Stats
    call_count = fields.Integer('Times Called', default=0)
    last_called = fields.Datetime('Last Called')
    error_count = fields.Integer('Errors', default=0)

    # Sandbox
    sandbox_enabled = fields.Boolean('Sandbox', default=True,
                                      help='Restrict code to safe operations')

    _sql_constraints = [
        ('name_unique', 'unique(name)', 'Tool name must be unique!'),
    ]

    @api.model
    def _ensure_builtin_tool_records(self, records=None):
        """Seeda ai.tool-poster för alla inbyggda verktyg (idempotent).

        Körs via data-XML (<function>) vid varje modul-init OCH -update.
        - Saknas post → skapas med builtin_name + metadata från core.
        - Finns post (t.ex. bridge-post browser_navigate) → sätts bara
          builtin_name om den saknas; befintlig konfiguration skrivs inte över.
        - Ingen code sätts — runtime-resolution använder den riktiga handlern.
        """
        from ..core.tools import builtin_tools
        _logger.info('Seeding builtin tool records (explicit-agent-tools)')
        existing = {t.name: t for t in self.search([])}
        created, marked = 0, 0
        for bt in builtin_tools():
            rec = existing.get(bt.name)
            if not rec:
                try:
                    # Savepoint isolerar create → en eventuell UniqueViolation
                    # (konkurrerande modulreload) dödar inte transaktionen.
                    with self.env.cr.savepoint():
                        rec = self.create({
                            'name': bt.name,
                            'builtin_name': bt.name,
                            'description': bt.description or bt.name,
                            'parameters': json.dumps(bt.parameters),
                            'risk_level': bt.risk_level or 'read_only',
                            'executor': bt.executor or 'local',
                            'capabilities': json.dumps(bt.capabilities or []),
                            'nats_subject': bt.nats_subject or 'pi.task.do',
                            'nats_skills': bt.nats_skills or '',
                            'active': True,
                        })
                    created += 1
                except Exception:
                    # Konkurrerande seedning har skapat posten — ok.
                    if self.search_count([('name', '=', bt.name)]):
                        continue
                    raise
            elif not rec.builtin_name:
                rec.write({'builtin_name': bt.name})
                marked += 1
            # Korrigera NATS-subject om posten har kvar default-värdet
            # (pi.task.do) men builtin definierar ett specifikt subject.
            # Bridge-poster med anpassad konfiguration skrivs inte över.
            if rec.executor == 'nats' and bt.nats_subject:
                if rec.nats_subject in (False, None, '', 'pi.task.do') \
                        and bt.nats_subject != 'pi.task.do':
                    rec.write({'nats_subject': bt.nats_subject})
                if rec.nats_skills in (False, None, '') and bt.nats_skills:
                    rec.write({'nats_skills': bt.nats_skills})
            # Bind xmlid (idempotent): tool_<sanitized name> i ai_agent_core.
            # Gör att data-XML (default_coworker.xml m.fl.) kan referera
            # verktygen med ref('tool_describe_model') etc.
            self._bind_tool_xmlid(rec)
        _logger.info('Builtin tool seed: %d skapade, %d markerade', created, marked)
        return True

    @api.model
    def _bind_tool_xmlid(self, rec):
        """Bind ai_agent_core.tool_<name> → ai.tool-post (idempotent)."""
        import re as _re
        xmlid_name = 'tool_' + _re.sub(r'[^a-zA-Z0-9_.-]', '_', rec.name)
        IrModelData = self.env['ir.model.data'].sudo()
        existing = IrModelData.search([
            ('module', '=', 'ai_agent_core'),
            ('name', '=', xmlid_name),
        ], limit=1)
        if existing:
            if existing.model != rec._name or existing.res_id != rec.id:
                existing.write({'model': rec._name, 'res_id': rec.id})
        else:
            IrModelData.create({
                'module': 'ai_agent_core',
                'name': xmlid_name,
                'model': rec._name,
                'res_id': rec.id,
                'noupdate': True,
            })

    @api.constrains('parameters')
    def _check_parameters_json(self):
        for rec in self:
            if rec.parameters:
                try:
                    params = json.loads(rec.parameters)
                    if not isinstance(params, dict):
                        raise UserError(_('Parameters must be a JSON object'))
                    if 'type' not in params:
                        raise UserError(_('Parameters must include "type" field'))
                except json.JSONDecodeError:
                    raise UserError(_('Parameters must be valid JSON'))

    def _filter_by_access_groups(self, group_ids):
        """Return only tools accessible to the given Odoo group ids.

        A tool is accessible when its group_ids is empty (unrestricted) or
        intersects the caller's groups. An empty basis (no known user)
        yields only unrestricted tools — group-bound tools are denied.
        """
        gids = set(group_ids or [])
        return self.filtered(
            lambda t: not t.group_ids or bool(gids & set(t.group_ids.ids)))

    def action_test(self):
        """Test the tool by executing it with empty/default parameters."""
        self.ensure_one()
        try:
            params = json.loads(self.parameters)
            test_args = {}
            for prop_name, prop_def in params.get('properties', {}).items():
                prop_type = prop_def.get('type', 'string')
                if prop_type == 'string':
                    test_args[prop_name] = ''
                elif prop_type == 'integer':
                    test_args[prop_name] = 0
                elif prop_type == 'boolean':
                    test_args[prop_name] = False
                elif prop_type == 'array':
                    test_args[prop_name] = []

            result = self._execute_tool(test_args)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Tool Test OK'),
                    'message': str(result)[:200],
                    'type': 'success',
                }
            }
        except Exception as e:
            self.error_count += 1
            raise UserError(_('Tool test failed: %s') % str(e))

    def _execute_tool(self, kwargs: dict) -> str:
        """Execute the tool code with given parameters."""
        self.ensure_one()

        # Sandboxed environment
        allowed_builtins = {
            'True': True, 'False': False, 'None': None,
            'str': str, 'int': int, 'float': float, 'bool': bool,
            'list': list, 'dict': dict, 'len': len,
            'range': range, 'enumerate': enumerate, 'zip': zip,
            '__import__': __import__,
            'print': lambda *a, **kw: None,  # no-op in sandbox
        }

        safe_globals = {
            '__builtins__': allowed_builtins,
            'env': self.env,
            'json': __import__('json'),
            'datetime': __import__('datetime'),
            'os': __import__('os'),
            're': __import__('re'),
        }

        try:
            # Compile and execute the tool code
            compiled = compile(self.code, f'<tool:{self.name}>', 'exec')
            exec(compiled, safe_globals)

            # Get the execute function
            execute_func = safe_globals.get('execute')
            if not execute_func:
                raise UserError(_('Tool must define an "execute" function'))

            if not callable(execute_func):
                raise UserError(_('"execute" must be a function'))

            # Call synchronously (Odoo is sync)
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # We're in an async context — use run_coroutine_threadsafe
                    result = asyncio.run_coroutine_threadsafe(
                        execute_func(**kwargs), loop
                    ).result(timeout=30)
                else:
                    result = loop.run_until_complete(execute_func(**kwargs))
            except RuntimeError:
                # No running loop — create one
                result = asyncio.run(execute_func(**kwargs))

            self.call_count += 1
            self.last_called = fields.Datetime.now()
            return str(result)

        except asyncio.TimeoutError:
            self.error_count += 1
            raise UserError(_('Tool execution timed out after 30s'))

        except Exception as e:
            self.error_count += 1
            _logger.error("Tool '%s' execution failed: %s", self.name, e, exc_info=True)
            raise UserError(_('Tool execution failed: %s') % str(e))

    def to_core_tool(self, env=None):
        """Convert to core.tools.Tool for use in AgentLoop.

        Returns a Tool dataclass instance compatible with the core loop.
        For NATS-executor tools, handler is None (execution via NATS).
        For builtin tools (builtin_name set), returns the real builtin Tool
        with its Python handler (explicit-agent-tools).
        """
        from ..core.tools import Tool
        import json

        # Builtin-verktyg: hämta den riktiga handlern från core.
        if self.builtin_name:
            from ..core.tools import builtin_tools
            for bt in builtin_tools():
                if bt.name == self.builtin_name:
                    return bt
            _logger.warning(
                'Builtin tool %s not found in builtin_tools() — faller '
                'tillbaka på custom-konvertering', self.builtin_name)

        params = json.loads(self.parameters) if self.parameters else {
            "type": "object", "properties": {}, "required": []
        }

        caps = []
        if self.capabilities:
            try:
                caps = json.loads(self.capabilities)
                if not isinstance(caps, list):
                    caps = []
            except (json.JSONDecodeError, TypeError):
                caps = []

        if self.executor == "nats":
            # NATS-executor tool — no handler code needed
            return Tool(
                name=self.name,
                description=self.description,
                parameters=params,
                handler=None,
                risk_level=self.risk_level,
                source="custom",
                executor="nats",
                capabilities=caps,
                nats_subject=self.nats_subject or "pi.task.do",
                nats_skills=self.nats_skills or "",
                nats_timeout=self.nats_timeout or 30,
            )

        # Local executor tool (existing behavior)
        async def handler(**kwargs):
            try:
                result = self._execute_tool(kwargs)
                return result
            except Exception as e:
                return f"Tool error ({self.name}): {e}"

        return Tool(
            name=self.name,
            description=self.description,
            parameters=params,
            handler=handler,
            risk_level=self.risk_level,
            source="custom",
            executor="local",
            capabilities=caps,
        )

    def action_open_form(self):
        """Öppna verktygets formulär (från listan)."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'ai.tool',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'current',
        }

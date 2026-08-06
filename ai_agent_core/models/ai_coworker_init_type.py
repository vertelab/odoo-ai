# -*- coding: utf-8 -*-
"""ai.coworker.init_type — multi-init-type support for ai.coworker.

One quest can have multiple active init types simultaneously.
Each type has its own configuration fields.
"""

from odoo import models, fields, api, _
from odoo.exceptions import UserError
import logging

_logger = logging.getLogger(__name__)


# En enda källa för init-type-valen: INIT_TYPES i ai_coworker.py.
# (controller är borttagen — legacy.) ai_coworker importeras före
# ai_coworker_init_type i models/__init__.py, så detta är inte cirkulärt.
from .ai_coworker import INIT_TYPES as INIT_TYPE_SELECTION


class AICoworkerInitType(models.Model):
    _name = 'ai.coworker.init_type'
    _description = 'AI Quest Init Type'
    _order = 'sequence asc, id asc'
    _rec_name = 'display_name'

    coworker_id = fields.Many2one("ai.coworker", required=True, ondelete='cascade',
                                string='Quest')
    init_type = fields.Selection(INIT_TYPE_SELECTION, required=True,
                                  string='Initiation Type')
    enabled = fields.Boolean('Enabled', default=True)
    sequence = fields.Integer(default=10)

    # Display name shows the function, not the quest name
    display_name = fields.Char(compute='_compute_display_name', store=True)

    @api.depends('init_type', 'coworker_id.name')
    def _compute_display_name(self):
        for r in self:
            label = dict(INIT_TYPE_SELECTION).get(r.init_type, r.init_type)
            status = ' (aktiv)' if r.enabled else ''
            r.display_name = label + status

    # ── web_ui specific ──
    show_in_chat = fields.Boolean('Show in Web Chat', default=True,
        help='When enabled, this quest appears in the /ai/chat interface.')

    # ── chat specific ──
    response_mode = fields.Selection([
        ('always', 'Always Respond'),
        ('mention', 'Only on @mention'),
        ('trigger', 'Trigger words only'),
    ], default='mention', string='Response Mode',
        help='How this quest responds in chat/channel messages')
    chat_user_id = fields.Many2one('res.users', string='Chat Bot User',
        readonly=True,
        help='Auto-created bot user for private Discuss chat')
    use_chat_history = fields.Boolean(default=True)
    chat_history_limit = fields.Integer(default=10)
    allow_trigger_words = fields.Boolean('Use Activation Words')
    chat_trigger_words = fields.Text('Activation Words',
        help='Comma-separated words that trigger the bot response')

    # ── channel specific ──
    channel_ids = fields.Many2many('discuss.channel', 'ai_coworker_init_type_channel_rel',
        'init_type_id', 'channel_id', string='Channels',
        help='Discuss channels where the bot participates')
    channel_id = fields.Many2one('discuss.channel', string='Channel (legacy)',
        help='Legacy single-channel field — kept for compatibility')
    channel_reply_mode = fields.Selection([
        ('public', 'Public (in channel)'),
        ('private', 'Private (direct message)'),
        ('thread', 'Thread reply'),
    ], default='public', string='Reply Mode',
        help='How the bot responds in channel messages')

    # ── mail specific ──
    alias_name = fields.Char('Email Alias',
        help='Local part of the email address (before @)')
    alias_id = fields.Many2one('mail.alias', string='Alias Record',
        readonly=True)
    alias_contact = fields.Selection([
        ('everyone', 'Everyone'),
        ('partners', 'Authenticated Partners'),
        ('followers', 'Followers only'),
        ('employees', 'Authenticated Employees'),
    ], default='everyone', string='Accept Emails From')

    # ── cron specific ──
    cron_id = fields.Many2one('ir.cron', string='Scheduled Action',
        ondelete='cascade')
    filter_domain = fields.Char('Record Filter',
        help='Domain applied to cron-triggered records')
    cron_interval_number = fields.Integer('Interval', default=1,
        help='How often to run (1 = every interval)')
    cron_interval_type = fields.Selection([
        ('minutes', 'Minutes'),
        ('hours', 'Hours'),
        ('days', 'Days'),
        ('weeks', 'Weeks'),
        ('months', 'Months'),
    ], default='hours', string='Interval Unit')

    # ── server_action specific ──
    server_action_id = fields.Many2one('ir.actions.server',
        string='Server Action', ondelete='cascade')
    server_action_use_wizard = fields.Boolean('Show Prompt Wizard',
        default=False,
        help='If enabled, a wizard pops up to let the user enter a prompt before executing')


    # ── openai_api specific ──
    rate_limit_rpm = fields.Integer('Rate Limit (req/min)', default=30)
    rate_limit_tpm = fields.Integer('Rate Limit (tokens/min)', default=100000)

    # ── watch specific ──
    watch_model_id = fields.Many2one('ir.model', string='Watch Model',
        help='Model to watch for data changes.')
    watch_trigger = fields.Selection([
        ('create', 'Create'),
        ('write', 'Write'),
        ('create_or_write', 'Create or Write'),
        ('delete', 'Delete'),
    ], string='Watch Trigger', default='create_or_write',
        help='Which action triggers the coworker.')
    watch_domain = fields.Char('Watch Domain',
        help='Domain filter for which records trigger. '
             'E.g. [("priority", ">", 5)]')
    base_automation_id = fields.Many2one('base.automation',
        string='Base Automation', readonly=True,
        help='Auto-created base.automation record.')

    @api.onchange('init_type')
    def _onchange_init_type(self):
        """Reset type-specific fields when init_type changes."""
        # Clear fields that don't belong to the new type
        type_fields = {
            'web_ui': [],
            'chat': ['response_mode', 'use_chat_history', 'chat_history_limit',
                     'allow_trigger_words', 'chat_trigger_words'],
            'channel': ['response_mode', 'channel_id', 'channel_ids', 'channel_reply_mode',
                     'allow_trigger_words', 'chat_trigger_words'],
            'mail': ['alias_name', 'alias_id', 'alias_contact'],
            'cron': ['cron_id', 'filter_domain', 'cron_interval_number', 'cron_interval_type'],
            'server_action': ['server_action_id', 'server_action_use_wizard'],
            'powerbox': [],
            'manual': [],
            'webhook': [],
            'controller': [],
            'openai_api': ['rate_limit_rpm', 'rate_limit_tpm'],
            'watch': ['watch_model_id', 'watch_trigger', 'watch_domain', 'base_automation_id'],
        }
        all_specific = set()
        for fields_list in type_fields.values():
            all_specific.update(fields_list)

        keep = set(type_fields.get(self.init_type, []))
        clear = all_specific - keep

        for fname in clear:
            if hasattr(self, fname):
                setattr(self, fname, False if fname.startswith('use_') or
                        fname.startswith('allow_') else None)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._after_change()
        return records

    def write(self, vals):
        res = super().write(vals)
        if 'init_type' in vals or 'enabled' in vals:
            self._after_change()
        # Deactivate cron when cron init_type is turned off
        if vals.get('enabled') is False or vals.get('enabled') == False:
            for record in self:
                if record.init_type == 'cron' and record.cron_id:
                    record.cron_id.active = False
        return res

    def _after_change(self):
        """Auto-create/update resources when init types change."""
        for record in self:
            if record.init_type == 'mail' and record.enabled:
                record._ensure_mail_alias()
            elif record.init_type == 'chat' and record.enabled:
                record._ensure_chat_user()
            elif record.init_type == 'channel' and record.enabled:
                record._ensure_channel()
            elif record.init_type == 'cron' and record.enabled:
                record._ensure_cron()
            elif record.init_type == 'server_action' and record.enabled:
                record._ensure_server_action()
            elif record.init_type == 'powerbox' and record.enabled:
                record._ensure_powerbox()
            elif record.init_type == 'webhook' and record.enabled:
                record._ensure_webhook()
            elif record.init_type == 'watch' and record.enabled:
                record._ensure_watch()

    # ── Resource auto-creation ──

    def _ensure_watch(self):
        """Create/update base.automation for watch init_type.

        The base.automation watches a model for data changes and
        triggers the coworker via a linked server action.
        """
        if not self.watch_model_id or not self.enabled:
            return

        trigger_map = {
            'create': 'on_create_or_write',
            'write': 'on_create_or_write',
            'create_or_write': 'on_create_or_write',
            'delete': 'on_unlink',
        }
        trigger = trigger_map.get(self.watch_trigger, 'on_create_or_write')
        auto_name = f'AI Watch: {self.coworker_id.name} on {self.watch_model_id.name}'

        try:
            if not self.base_automation_id:
                # Create base.automation
                automation = self.env['base.automation'].create({
                    'name': auto_name,
                    'model_id': self.watch_model_id.id,
                    'trigger': trigger,
                    'filter_domain': self.watch_domain or '',
                    'active': True,
                })
                # Create the linked server action
                action = self.env['ir.actions.server'].create({
                    'name': auto_name,
                    'model_id': self.watch_model_id.id,
                    'base_automation_id': automation.id,
                    'state': 'code',
                    'code': (
                        "records = env['ai.coworker.init_type']"
                        f".browse({self.id})._trigger_watch(records)\n"
                    ),
                })
                self.base_automation_id = automation.id
                _logger.info('Created base_automation %s for watch init', auto_name)
            else:
                # Update existing automation
                self.base_automation_id.write({
                    'name': auto_name,
                    'model_id': self.watch_model_id.id,
                    'trigger': trigger,
                    'filter_domain': self.watch_domain or '',
                    'active': True,
                })
        except Exception as e:
            _logger.warning('Failed to ensure watch for %s: %s',
                          self.coworker_id.name if self.coworker_id else '?', e)

    def _trigger_watch(self, records):
        """Called by base_automation when watched data changes.

        Creates a session and runs the coworker with the changed record
        as context.
        """
        self.ensure_one()
        coworker = self.coworker_id
        if not coworker or not records:
            return

        # Budget check before acting
        try:
            warning, exhausted = coworker.check_cap()
            if exhausted:
                _logger.info('Watch %s skipped: budget exhausted', coworker.name)
                return
        except Exception:
            pass

        for record in records[:3]:  # Max 3 records per trigger
            try:
                session = self.env['ai.coworker.session'].create({
                    'coworker_id': coworker.id,
                    'name': f'Watch: {record._name} {record.id}',
                    'status': 'active',
                    'user_id': self.env.ref('base.user_root').id,
                })
                prompt = (
                    f'A data change was detected on record '
                    f'{record.display_name or record.id} ({record._name}).\n'
                    f'Review the record and take appropriate action.\n\n'
                    f'Record details:\n{record.name or record.display_name or record.id}'
                )
                coworker.run(session=session, prompt=prompt, record=record)
                _logger.info('Watch %s: processed %s %s',
                            coworker.name, record._name, record.id)
            except Exception as e:
                _logger.error('Watch processing failed for %s: %s',
                            coworker.name, e)

    def _ensure_mail_alias(self):
        """Create mail alias if not exists."""
        if not self.alias_name:
            self.alias_name = self.coworker_id.name.lower().replace(' ', '-')
        if not self.alias_id:
            alias = self.env['mail.alias'].create({
                'alias_name': self.alias_name,
                'alias_model_id': self.env['ir.model']._get('ai.coworker.session').id,
                'alias_defaults': {'ai_coworker_id': self.coworker_id.id},
                'alias_contact': self.alias_contact,
            })
            self.alias_id = alias.id

    def _ensure_chat_user(self):
        """Create bot user for private chat if not exists."""
        if not self.chat_user_id:
            quest = self.coworker_id
            user = self.env['res.users'].search([
                ('name', '=', quest.name),
                ('login', '=', 'bot_' + quest.name.lower().replace(' ', '_')),
            ], limit=1)
            if not user:
                user = self.env['res.users'].with_context(
                    no_reset_password=True).create({
                        'name': quest.name,
                        'login': 'bot_' + quest.name.lower().replace(' ', '_'),
                    })
            self.chat_user_id = user.id

    def _ensure_channel(self):
        """Create Discuss channel if not exists and add to channel_ids.

        Also syncs the coworker into each channel's ai_coworker_ids
        so @mention routing works.
        """
        if not self.channel_ids:
            channel = self.env['discuss.channel'].create({
                'name': self.coworker_id.name,
            })
            self.channel_ids = [(4, channel.id)]
        # Keep legacy channel_id in sync for backward compat
        if not self.channel_id and self.channel_ids:
            self.channel_id = self.channel_ids[0].id

        # Sync coworker into channel's ai_coworker_ids for @mention routing
        coworker = self.coworker_id
        for channel in self.channel_ids:
            if coworker not in channel.ai_coworker_ids:
                channel.write({'ai_coworker_ids': [(4, coworker.id)]})
                _logger.info(
                    "Added coworker %s to channel %s ai_coworker_ids",
                    coworker.name, channel.name,
                )
        channel._sync_ai_coworker_members()

    def _ensure_cron(self):
        """Create ir.cron record if not exists, using interval from config."""
        try:
            if not self.cron_id:
                # Determine target model from coworker's model_ids, fallback to res.partner
                try:
                    model_id = self.env.ref('base.model_res_partner').id
                except Exception:
                    model_id = False
                if self.coworker_id and self.coworker_id.model_ids:
                    model_id = self.coworker_id.model_ids[0].id

                if model_id:
                    cron = self.env['ir.cron'].create({
                        'name': self.coworker_id.name,
                        'model_id': model_id,
                        'state': 'code',
                        'code': f"env.ref('{self.coworker_id._get_eid()}').action_run_scheduled()",
                        'numbercall': -1,
                        'interval_number': self.cron_interval_number or 1,
                        'interval_type': self.cron_interval_type or 'hours',
                        'active': True,
                    })
                    self.cron_id = cron.id
            else:
                # Update existing cron with current interval values
                self.cron_id.write({
                    'interval_number': self.cron_interval_number or 1,
                    'interval_type': self.cron_interval_type or 'hours',
                })
        except Exception as e:
            _logger.warning('Failed to ensure cron for %s: %s',
                          self.coworker_id.name if self.coworker_id else '?', e)

    def _ensure_powerbox(self):
        """Ensure powerbox init_type has model_ids configured."""
        coworker = self.coworker_id
        if not self.coworker_id.model_ids:
            _logger.info(
                "Powerbox init_type for %s has no model_ids — "
                "configure models on the coworker to enable powerbox",
                coworker.name,
            )

    def _ensure_webhook(self):
        """Ensure webhook is configured — generate secret if missing."""
        coworker = self.coworker_id
        if not coworker.webhook_secret:
            import secrets
            coworker.webhook_secret = secrets.token_hex(16)
            _logger.info(
                "Auto-generated webhook secret for coworker %s",
                coworker.name,
            )

    def _ensure_server_action(self):
        """Create server action and bind to models.

        If wizard mode is enabled, the server action opens a wizard
        instead of running the agent directly.
        """
        if not self.server_action_id and self.coworker_id.model_ids:
            if self.server_action_use_wizard:
                # Wizard mode: open the prompt wizard
                code = (
                    "action = env['ir.actions.act_window']._for_xml_id("
                    f"'ai_agent_core.ai_coworker_server_action_wizard_action')\n"
                    "action['context'] = dict(env.context,\n"
                    "    default_coworker_id=" + str(self.coworker_id.id) + ",\n"
                    "    default_res_model='" + str(self.coworker_id.model_ids[0].model) + "',\n"
                    "    default_res_id=records[0].id if records else False,\n"
                    "    default_res_model_name=records[0].display_name if records else '',\n"
                    ")\n"
                    "return action"
                )
            else:
                # Direct mode: run immediately
                code = f"env.ref('{self.coworker_id._get_eid()}').server_action(records)"

            action = self.env['ir.actions.server'].create({
                'name': self.coworker_id.name,
                'model_id': self.coworker_id.model_ids[0].id,
                'binding_model_ids': [(6, 0, self.coworker_id.model_ids.ids)],
                'binding_view_types': 'form,list',
                'binding_type': 'action',
                'state': 'code',
                'code': code,
            })
            self.server_action_id = action.id

    def unlink(self):
        """Clean up auto-created resources."""
        for record in self:
            if record.chat_user_id and record.chat_user_id.login.startswith('bot_'):
                record.chat_user_id.active = False
            if record.channel_ids:
                # Remove coworker from channel's ai_coworker_ids
                coworker = record.coworker_id
                for ch in record.channel_ids:
                    if coworker in ch.ai_coworker_ids:
                        ch.write({'ai_coworker_ids': [(3, coworker.id)]})
                record.channel_ids.write({'active': False})
            if record.channel_id:
                record.channel_id.active = False
            if record.cron_id:
                record.cron_id.active = False
            if record.server_action_id:
                record.server_action_id.active = False
            if record.base_automation_id:
                record.base_automation_id.active = False
        return super().unlink()

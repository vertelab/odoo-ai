# -*- coding: utf-8 -*-
"""ai.coworker.init_type — multi-init-type support for ai.coworker.

One quest can have multiple active init types simultaneously.
Each type has its own configuration fields.
"""

from odoo import models, fields, api, _
from odoo.exceptions import UserError


INIT_TYPE_SELECTION = [
    ('web_ui', 'Web Chat UI'),
    ('chat', 'Discuss — Private Chat'),
    ('channel', 'Discuss — Team Channel'),
    ('mail', 'Incoming Mail'),
    ('cron', 'Scheduled Action'),
    ('server_action', 'Server Action'),
    ('powerbox', 'Powerbox'),
    ('manual', 'Manual'),
    ('openai_api', 'OpenAI API'),
]


class AIQuestInitType(models.Model):
    _name = 'ai.coworker.init_type'
    _description = 'AI Quest Init Type'
    _order = 'sequence asc, id asc'
    _rec_name = 'display_name'

    quest_id = fields.Many2one('ai.coworker', required=True, ondelete='cascade',
                                string='Quest')
    init_type = fields.Selection(INIT_TYPE_SELECTION, required=True,
                                  string='Initiation Type')
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)

    # Display name shows the function, not the quest name
    display_name = fields.Char(compute='_compute_display_name', store=True)

    @api.depends('init_type', 'quest_id.name')
    def _compute_display_name(self):
        for r in self:
            label = dict(INIT_TYPE_SELECTION).get(r.init_type, r.init_type)
            status = ' (aktiv)' if r.active else ''
            r.display_name = label + status

    # ── web_ui specific ──
    show_in_chat = fields.Boolean('Show in Web Chat', default=True,
        help='When enabled, this quest appears in the /ai/chat interface.')

    # ── chat specific ──
    chat_user_id = fields.Many2one('res.users', string='Chat Bot User',
        readonly=True,
        help='Auto-created bot user for private Discuss chat')
    use_chat_history = fields.Boolean(default=True)
    chat_history_limit = fields.Integer(default=10)
    allow_trigger_words = fields.Boolean('Use Activation Words')
    chat_trigger_words = fields.Text('Activation Words',
        help='Comma-separated words that trigger the bot response')

    # ── channel specific ──
    channel_id = fields.Many2one('discuss.channel', string='Channel',
        help='Discuss channel where the bot participates')

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

    # ── server_action specific ──
    server_action_id = fields.Many2one('ir.actions.server',
        string='Server Action', ondelete='cascade')

    # ── openai_api specific ──
    api_key_attachment_id = fields.Many2one('ir.attachment',
        string='API Key',
        help='Stored API key for OpenAI-compatible endpoint access')
    rate_limit_rpm = fields.Integer('Rate Limit (req/min)', default=30)
    rate_limit_tpm = fields.Integer('Rate Limit (tokens/min)', default=100000)

    @api.onchange('init_type')
    def _onchange_init_type(self):
        """Reset type-specific fields when init_type changes."""
        # Clear fields that don't belong to the new type
        type_fields = {
            'web_ui': [],
            'chat': ['use_chat_history', 'chat_history_limit',
                     'allow_trigger_words', 'chat_trigger_words'],
            'channel': ['channel_id', 'allow_trigger_words', 'chat_trigger_words'],
            'mail': ['alias_name', 'alias_id', 'alias_contact'],
            'cron': ['cron_id', 'filter_domain'],
            'server_action': ['server_action_id'],
            'powerbox': [],
            'manual': [],
            'openai_api': ['api_key_attachment_id', 'rate_limit_rpm', 'rate_limit_tpm'],
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
        if 'init_type' in vals or 'active' in vals:
            self._after_change()
        return res

    def _after_change(self):
        """Auto-create/update resources when init types change."""
        for record in self:
            if record.init_type == 'mail' and record.active:
                record._ensure_mail_alias()
            elif record.init_type == 'chat' and record.active:
                record._ensure_chat_user()
            elif record.init_type == 'channel' and record.active:
                record._ensure_channel()
            elif record.init_type == 'cron' and record.active:
                record._ensure_cron()
            elif record.init_type == 'server_action' and record.active:
                record._ensure_server_action()

    # ── Resource auto-creation ──

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
        """Create Discuss channel if not exists."""
        if not self.channel_id:
            channel = self.env['discuss.channel'].create({
                'name': self.coworker_id.name,
            })
            self.channel_id = channel.id

    def _ensure_cron(self):
        """Create ir.cron record if not exists."""
        if not self.cron_id:
            cron = self.env['ir.cron'].create({
                'name': self.coworker_id.name,
                'model_id': self.env.ref('base.model_res_partner').id,
                'state': 'code',
                'code': f"env.ref('{self.coworker_id._get_eid()}').action_run_scheduled()",
                'numbercall': -1,
            })
            self.cron_id = cron.id

    def _ensure_server_action(self):
        """Create server action and bind to models."""
        if not self.server_action_id and self.coworker_id.model_ids:
            action = self.env['ir.actions.server'].create({
                'name': self.coworker_id.name,
                'model_id': self.coworker_id.model_ids[0].id,
                'binding_model_ids': [(6, 0, self.coworker_id.model_ids.ids)],
                'binding_view_types': 'form,list',
                'binding_type': 'action',
                'state': 'code',
                'code': f"env.ref('{self.coworker_id._get_eid()}').server_action(records)",
            })
            self.server_action_id = action.id

    def unlink(self):
        """Clean up auto-created resources."""
        for record in self:
            if record.chat_user_id and record.chat_user_id.login.startswith('bot_'):
                record.chat_user_id.active = False
            if record.channel_id:
                record.channel_id.active = False
            if record.cron_id:
                record.cron_id.active = False
            if record.server_action_id:
                record.server_action_id.active = False
        return super().unlink()

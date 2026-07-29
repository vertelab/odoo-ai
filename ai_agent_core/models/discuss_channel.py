# -*- coding: utf-8 -*-
"""Discuss channel extensions for Buzz workspace support."""

from odoo import models, fields, api


class DiscussChannel(models.Model):
    _inherit = 'discuss.channel'

    ai_agent_ids = fields.Many2many(
        'ai.agent', 'discuss_channel_ai_agent_rel',
        'channel_id', 'agent_id',
        string='AI Agents',
        help='AI agents that are visible members of this channel via Buzz workspaces.')
    ai_coworker_ids = fields.Many2many(
        'ai.coworker', 'discuss_channel_ai_coworker_rel',
        'channel_id', 'coworker_id',
        string='AI Coworkers',
        help='AI coworkers that are visible members of this channel.')
    ai_coworker_id = fields.Many2one(
        'ai.coworker', string='Buzz Quest',
        help='The Buzz workspace quest linked to this channel, if any.')

    def _sync_ai_agent_members(self):
        """Ensure ai.agent partners are present in channel members."""
        for channel in self:
            if channel.channel_type != 'channel':
                continue
            current_partners = channel.channel_member_ids.mapped('partner_id')
            for agent in channel.ai_agent_ids:
                if agent.partner_id and agent.partner_id not in current_partners:
                    self.env['discuss.channel.member'].sudo().create({
                        'channel_id': channel.id,
                        'partner_id': agent.partner_id.id,
                    })

    def _sync_ai_coworker_members(self):
        """Ensure ai.coworker partners are present in channel members."""
        for channel in self:
            if channel.channel_type != 'channel':
                continue
            current_partners = channel.channel_member_ids.mapped('partner_id')
            for coworker in channel.ai_coworker_ids:
                coworker._ensure_partner()
                if coworker.partner_id and coworker.partner_id not in current_partners:
                    self.env['discuss.channel.member'].sudo().create({
                        'channel_id': channel.id,
                        'partner_id': coworker.partner_id.id,
                    })

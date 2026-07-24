# -*- coding: utf-8 -*-
"""ai.memory — FAISS/pgvector memory for agents."""

import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AIMemory(models.Model):
    _name = 'ai.memory'
    _description = 'AI Memory'
    _order = 'create_date desc'

    name = fields.Char('Memory Key')
    content = fields.Text('Content')
    memory_type = fields.Selection([
        ('faiss', 'FAISS Vector'),
        ('pgvector', 'pgvector'),
        ('text', 'Plain Text'),
    ], default='text')

    # Embedding
    embedding_model = fields.Char('Embedding Model')
    embedding_vector = fields.Text('Vector (base64)')

    # Relations
    identity_id = fields.Many2one('ai.identity', string='Identity')
    agent_id = fields.Many2one('ai.agent', string='Agent')

    # Quest learning (quest-learning-memory)
    quest_id = fields.Many2one('ai.quest', string='Quest',
                                help='The quest this memory belongs to')
    category = fields.Selection([
        ('preference', 'User Preference'),
        ('fact', 'Key Fact'),
        ('correction', 'Correction'),
        ('pattern', 'Pattern'),
        ('feedback', 'Feedback'),
    ], string='Category')
    source_thread_id = fields.Many2one('ai.quest.session', string='Source Thread',
                                        help='Thread where this memory was extracted')
    consolidated = fields.Boolean('Consolidated', default=False,
                                   help='Included in system prompt after consolidation')
    archived = fields.Boolean('Archived', default=False,
                               help='Hidden from system prompt injection')

    # Metadata
    tags = fields.Char('Tags', help='Comma-separated')
    importance = fields.Selection([
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'),
    ], default='medium')

    # Timestamps
    last_accessed = fields.Datetime()
    access_count = fields.Integer(default=0)

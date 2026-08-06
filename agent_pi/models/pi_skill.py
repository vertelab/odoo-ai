# Copyright (C) 2026 Vertel AB (<https://vertel.se>).
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""
Pi Agent Skills — Markdown instructions sent to Pi agents.

Inspired by module.skill from module_catalog.
Skills define an agent's role, capabilities, and workflow.
They are sent alongside tasks via NATS.
"""

from odoo import api, fields, models
from odoo import _
import markdown


class PiSkillCategory(models.Model):
    _name = "pi.skill.category"
    _description = "Skill Category"
    _order = "sequence, name"

    name = fields.Char(string="Name", required=True, translate=True)
    technical_name = fields.Char(string="Technical Name", index=True)
    sequence = fields.Integer(string="Sequence", default=10)
    active = fields.Boolean(string="Active", default=True)
    description = fields.Text(string="Description", translate=True)
    skill_ids = fields.One2many("pi.skill", "category_id", string="Skills")
    skill_count = fields.Integer(
        string="Skills", compute="_compute_skill_count", store=True
    )

    def _compute_skill_count(self):
        for rec in self:
            rec.skill_count = len(rec.skill_ids)


class PiSkill(models.Model):
    _name = "pi.skill"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Pi Agent Skill"
    _order = "sequence, name"

    name = fields.Char(string="Name", required=True, translate=True)
    technical_name = fields.Char(string="Technical Name", index=True)
    category_id = fields.Many2one(
        "pi.skill.category", string="Category", required=True, ondelete="cascade",
    )
    sequence = fields.Integer(string="Sequence", default=10)
    active = fields.Boolean(string="Active", default=True)
    description = fields.Text(string="Description", translate=True)
    instruction = fields.Text(
        string="Instruction",
        required=True,
        help="Markdown prompt/instruction for the Pi agent",
    )
    example = fields.Text(
        string="Example",
        help="Example usage or expected output",
    )
    agent_ids = fields.Many2many(
        "pi.agent", string="Agents",
        help="Agents that have this skill available",
    )
    task_ids = fields.Many2many(
        "pi.task", string="Tasks",
        help="Tasks that use this skill",
    )
    color = fields.Integer(string="Color")
    notes = fields.Text(string="Internal Notes")
    markdown_content = fields.Html(
        string="Markdown Content",
        help="Full SKILL.md content in Markdown format. Rendered as HTML.",
        sanitize=False,
    )
    markdown_rendered = fields.Html(
        string="Rendered Preview",
        compute="_compute_markdown_rendered",
        sanitize=False,
        help="Markdown rendered as HTML for preview",
    )

    @api.depends("markdown_content")
    def _compute_markdown_rendered(self):
        for rec in self:
            if rec.markdown_content:
                rec.markdown_rendered = markdown.markdown(
                    rec.markdown_content,
                    extensions=["fenced_code", "tables", "codehilite"],
                )
            else:
                rec.markdown_rendered = False

    def action_view(self):
        """Open the skill form view."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Skill"),
            "res_model": "pi.skill",
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

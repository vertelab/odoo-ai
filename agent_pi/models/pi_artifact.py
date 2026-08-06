# Copyright (C) 2026 Vertel AB (<https://vertel.se>).
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

"""
Pi Artifact — results, logs, images from Pi agent tasks.

Artifacts are created when a task result comes back via the NATS callback.
They can be logs, images (screenshots), files, or JSON data.
"""

from odoo import fields, models


class PiArtifact(models.Model):
    _name = "pi.artifact"
    _description = "Pi Task Artifact"
    _order = "sequence, id"

    task_id = fields.Many2one(
        "pi.task", string="Task", required=True, ondelete="cascade",
    )
    name = fields.Char(string="Name", required=True)
    artifact_type = fields.Selection(
        [
            ("log", "Log"),
            ("image", "Image"),
            ("file", "File"),
            ("json", "JSON"),
            ("text", "Text"),
        ],
        string="Type",
        default="text",
        required=True,
    )
    content = fields.Text(string="Content", help="Text content (for log/json/text types)")
    file_data = fields.Binary(string="File Data", help="Binary data (for image/file types)")
    filename = fields.Char(string="Filename")
    mimetype = fields.Char(string="MIME Type")
    url = fields.Char(string="URL", help="External URL to the artifact")
    sequence = fields.Integer(string="Sequence", default=10)
    notes = fields.Text(string="Notes")

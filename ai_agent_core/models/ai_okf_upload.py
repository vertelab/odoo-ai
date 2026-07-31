# -*- coding: utf-8 -*-
"""ai.okf.upload — uppladdningskön (task 5b.1–5b.5).

Web UI-uppladdning: async-kö med progress-visualisering (arbetar → ✓/✗).
Channel/chat: synkron indexering. Multimodal normalisering via
coworkerns agentförmågor (ai.skill/ai.tool). Resultatet ägs primärt
av res.users som laddade upp; coworkern är utförare.
"""

import logging
import os

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AIOkfUpload(models.Model):
    _name = 'ai.okf.upload'
    _description = 'OKF Upload'
    _order = 'create_date desc'

    name = fields.Char(string='Filnamn', required=True)
    attachment_id = fields.Many2one('ir.attachment', string='Bilaga')
    state = fields.Selection([
        ('queued', 'Köad'),
        ('working', 'Arbetar…'),
        ('done', 'Klar ✓'),
        ('error', 'Fel ✗'),
    ], string='Status', default='queued')

    # Ägande/access (task 5b.5)
    owner_user_id = fields.Many2one(
        'res.users', string='Uppladdare',
        help='Primär ägare — sessionen är knuten till en res.users.')
    owner_company_id = fields.Many2one(
        'res.company', string='Företag',
        help='Company-scope för channel-uppladdningar.')
    coworker_id = fields.Many2one(
        'ai.coworker', string='Coworker (utförare)',
        help='Coworkern vars agenter utför normaliseringen.')
    channel_id = fields.Many2one(
        'discuss.channel', string='Kanal',
        help='För channel-uppladdningar — access via kanalens prenumeranter.')

    error_message = fields.Text(
        string='Felmeddelande',
        help='Mouse-over/status-text: time-out / för stor / saknar agentisk '
             'förmåga / normaliseringsfel (task 7.11).')
    normalized_text = fields.Text(
        string='Normaliserad text (cache)',
        help='Normaliserad text som sources[].normalized_text-cache — '
             'metadata, INTE concept-innehåll (task 5b.4).')
    concept_ids = fields.Many2many('ai.okf.concept', string='Koncept')
    retry_count = fields.Integer(string='Retries', default=0)

    # ── Multimodal normalisering (task 5b.3) ──
    @api.model
    def _get_mime_category(self, mimetype):
        """Kategorisera MIME → normaliseringsstrategi."""
        if not mimetype:
            return 'text'
        if mimetype.startswith('text/'):
            return 'text'
        if mimetype == 'application/pdf':
            return 'pdf'
        if mimetype in ('application/vnd.openxmlformats-officedocument.'
                        'wordprocessingml.document',
                        'application/msword'):
            return 'docx'
        if mimetype.startswith('image/'):
            return 'image'
        if mimetype.startswith('audio/'):
            return 'audio'
        return 'text'

    def _has_capability(self, capability):
        """Har coworkerns agenter förmågan? (ai.skill/ai.tool)"""
        self.ensure_one()
        if not self.coworker_id:
            return False
        skills = self.coworker_id.skill_ids or self.coworker_id.agent_ids.mapped(
            'agent_id.skill_ids')
        names = set()
        for s in skills:
            names.add((s.name or '').lower())
            names.add((s.ai_skill_id.name or '').lower()
                      if hasattr(s, 'ai_skill_id') else '')
        tools = self.coworker_id.agent_ids.mapped('agent_id.tool_ids')
        for t in tools:
            names.add((t.name or '').lower())
        return capability.lower() in {n for n in names if n}

    def _normalize(self):
        """Normalisera artefakten till text. Returnerar (text, error)."""
        self.ensure_one()
        attach = self.attachment_id
        if not attach:
            return '', 'Bilagan saknas.'
        if attach.file_size and attach.file_size > 50 * 1024 * 1024:
            return '', 'Filen är för stor (max 50 MB).'

        category = self._get_mime_category(attach.mimetype)
        text = ''
        try:
            if category == 'text':
                text = attach._index_content() or ''
            elif category == 'pdf':
                text = self._normalize_pdf(attach)
            elif category == 'docx':
                text = self._normalize_docx(attach)
            elif category == 'image':
                text = self._normalize_image(attach)
            elif category == 'audio':
                text = self._normalize_audio(attach)
        except Exception as e:
            _logger.warning('OKF normalize failed for %s: %s', attach.name, e)
            return '', 'Normaliseringsfel: %s' % e

        if not text.strip():
            return '', 'Kunde inte extrahera text från filen.'
        return text, None

    def _normalize_pdf(self, attach):
        """PDF → pypdf/fitz (scannad → tesseract OCR)."""
        if not self._has_capability('pdf'):
            return self._raise_missing('pdf')
        try:
            import fitz  # PyMuPDF
            data = attach.raw
            doc = fitz.open(stream=data, filetype='pdf')
            parts = []
            for page in doc:
                parts.append(page.get_text())
            joined = '\n'.join(parts)
            if joined.strip():
                return joined
            # Scannad PDF → OCR
            if self._has_capability('ocr'):
                import subprocess
                tmp = '/tmp/okf_upload_%s.pdf' % self.id
                with open(tmp, 'wb') as f:
                    f.write(data)
                try:
                    out = subprocess.run(
                        ['pdftotext', tmp, '-'], capture_output=True,
                        text=True, timeout=120)
                    if out.stdout.strip():
                        return out.stdout
                finally:
                    if os.path.exists(tmp):
                        os.remove(tmp)
            return joined
        except ImportError:
            return self._raise_missing('pdf-lib')
        except Exception:
            raise

    def _normalize_docx(self, attach):
        """docx → python-docx."""
        if not self._has_capability('docx'):
            return self._raise_missing('docx')
        try:
            import io
            from docx import Document
            doc = Document(io.BytesIO(attach.raw))
            return '\n'.join(p.text for p in doc.paragraphs)
        except ImportError:
            return self._raise_missing('docx-lib')

    def _normalize_image(self, attach):
        """Bild → tesseract OCR + vision-caption (gpt-4o)."""
        if not self._has_capability('image'):
            return self._raise_missing('image')
        import base64
        import tempfile
        parts = []
        # 1. OCR
        if self._has_capability('ocr'):
            try:
                import subprocess
                with tempfile.NamedTemporaryFile(suffix='.img', delete=False) as f:
                    f.write(attach.raw)
                    tmp = f.name
                try:
                    out = subprocess.run(
                        ['tesseract', tmp, 'stdout'], capture_output=True,
                        text=True, timeout=120)
                    if out.stdout.strip():
                        parts.append('OCR-text:\n' + out.stdout)
                finally:
                    if os.path.exists(tmp):
                        os.remove(tmp)
            except Exception as e:
                _logger.warning('OCR failed: %s', e)
        # 2. Vision-caption (via AI-provider)
        if self._has_capability('vision'):
            try:
                provider = self.env['ai.provider'].search(
                    [('active', '=', True)], limit=1)
                if provider and hasattr(provider, '_generate_vision_caption'):
                    caption = provider._generate_vision_caption(
                        attach.raw)
                    if caption:
                        parts.append('Bildbeskrivning:\n' + caption)
            except Exception as e:
                _logger.warning('Vision caption failed: %s', e)
        if not parts:
            return self._raise_missing('vision-eller-ocr')
        return '\n\n'.join(parts)

    def _normalize_audio(self, attach):
        """Ljud → whisper-1-transkript (via ffmpeg)."""
        if not self._has_capability('whisper'):
            return self._raise_missing('whisper')
        try:
            import subprocess
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.audio', delete=False) as f:
                f.write(attach.raw)
                tmp = f.name
            try:
                # Konvertera till wav via ffmpeg, sedan transkribera via
                # OpenAI whisper-1 (via providern)
                wav = '/tmp/okf_upload_%s.wav' % self.id
                subprocess.run(
                    ['ffmpeg', '-y', '-i', tmp, '-ar', '16000', '-ac', '1',
                     wav], capture_output=True, timeout=300)
                provider = self.env['ai.provider'].search(
                    [('active', '=', True)], limit=1)
                if provider and hasattr(provider, '_transcribe_audio'):
                    with open(wav, 'rb') as f:
                        return provider._transcribe_audio(f.read()) or ''
                return self._raise_missing('whisper-provider')
            finally:
                for p in (tmp, '/tmp/okf_upload_%s.wav' % self.id):
                    if os.path.exists(p):
                        os.remove(p)
        except ImportError:
            return self._raise_missing('whisper-lib')

    def _raise_missing(self, capability):
        raise UserError(_(
            'Förmågan "%s" saknas på coworkerns agenter (felkonfiguration). '
            'Välj en annan coworker eller lägg till förmågan.'
        ) % capability)

    # ── Kön (task 5b.1) ──
    @api.model
    def _enqueue_upload(self, attachment, owner_user_id=None,
                        owner_company_id=None, coworker_id=None,
                        channel_id=None):
        """Skapa en kö-post för en uppladdad bilaga."""
        return self.create({
            'name': attachment.name,
            'attachment_id': attachment.id,
            'owner_user_id': owner_user_id or self.env.user.id,
            'owner_company_id': owner_company_id,
            'coworker_id': coworker_id,
            'channel_id': channel_id,
        })

    def _process_upload(self, sync=False):
        """Processa uppladdningen → normalisera → _okf_upsert().

        sync=True: synkron (channel/chat); sync=False: async (kö).
        """
        for rec in self:
            if rec.state == 'done':
                continue
            rec.state = 'working'
            try:
                text, error = rec._normalize()
                if error:
                    rec.write({'state': 'error', 'error_message': error})
                    continue
                rec.normalized_text = text[:100000]  # cache (5b.4)

                atype = self.env.ref(
                    'ai_agent_core.artifact_type_document',
                    raise_if_not_found=False) or 'document'
                concept = self.env['ai.okf.concept']._okf_upsert(
                    artifact_type=atype,
                    concept_key='ir.attachment,%s' % rec.attachment_id.id,
                    summary=text[:4000],  # tunt koncept
                    title=rec.name,
                    source_ref='ir.attachment,%s' % rec.attachment_id.id,
                    sources=[{
                        'resource': 'ir.attachment,%s' % rec.attachment_id.id,
                        'normalized_text': text[:100000],
                    }],
                    owner_company_id=rec.owner_company_id.id or None,
                    owner_user_id=rec.owner_user_id.id or None,
                    owner_coworker_id=rec.coworker_id.id or None,
                    generated_by='upload',
                )
                rec.write({'state': 'done'})
                rec.concept_ids = [(4, concept.id)]
            except UserError as e:
                rec.write({'state': 'error', 'error_message': str(e)})
            except Exception as e:
                _logger.exception('OKF upload failed for %s', rec.name)
                rec.write({'state': 'error',
                           'error_message': 'Internt fel: %s' % e})

    @api.model
    def _cron_process_uploads(self):
        """Async-kö (task 5b.1): processa köade uppladdningar i bakgrunden."""
        queued = self.search([('state', 'in', ('queued', 'working'))],
                             limit=20, order='create_date asc')
        queued._process_upload(sync=False)
        return len(queued)

    # ── Synkron väg för channel/chat (task 5b.2) ──
    @api.model
    def _process_channel_upload(self, attachment, coworker_id=None,
                                channel_id=None, author_id=None):
        """Synkron indexering vid attachment i discuss/session."""
        upload = self._enqueue_upload(
            attachment,
            owner_user_id=author_id or self.env.user.id,
            owner_company_id=self.env.company.id,
            coworker_id=coworker_id,
            channel_id=channel_id,
        )
        upload._process_upload(sync=True)
        return upload

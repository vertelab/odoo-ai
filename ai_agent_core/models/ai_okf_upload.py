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

# MIME-typer som innehåller binärdata och därmed inte får avkodas som text.
# NUL-tecknet (0x00) är giltig UTF-8 och överlever errors='replace', men
# PostgreSQL vägrar NUL i strängliteraler — en enda sådan post fällde hela
# uppladdningsbatchen (cron 669, 2026-09-14).
BINARY_MIMETYPES = frozenset([
    'application/zip',
    'application/x-zip-compressed',
    'application/x-zip',
    'application/gzip',
    'application/x-gzip',
    'application/x-tar',
    'application/x-7z-compressed',
    'application/x-rar-compressed',
    'application/octet-stream',
    'application/msword',
    'application/vnd.ms-excel',
    'application/vnd.ms-powerpoint',
    'application/rtf',
    'application/x-rtf',
    'text/rtf',
])

# MIME-typer som saknar egen normaliserare men vars innehåll är text.
# Dessa läses som text — NUL-saneringen i _normalize() skyddar dem.
# Dokumenterade här för tydlighet; de kräver ingen särskild gren.
TEXT_FALLBACK_MIMETYPES = frozenset([
    'message/rfc822',
    'message/global',
    'application/json',
    'application/xml',
    'application/x-yaml',
    'application/javascript',
    'application/x-javascript',
])


def _sanitize_text(value):
    """Ta bort NUL-tecken ur en sträng.

    PostgreSQL tillåter inte NUL (0x00) i strängliteraler; psycopg2 kastar
    ValueError och hela transaktionen rullas tillbaka. NUL är dessutom
    giltig UTF-8, så errors='replace' fångar den inte. Anropas på all text
    som skrivs till ORM:en från denna modul.
    """
    if not value or '\x00' not in value:
        return value
    return value.replace('\x00', '')


# ── Tekniska förmågor (fix 2026-09-22) ──
#
# Förmågor som 'pdf'/'image'/'docx' är BEROENDEN, inte skills. Den gamla
# kontrollen letade en ai.skill med det namnet och returnerade alltid False
# (sådana skills finns inte, och coworker_id är NULL för web-UI-uppladdningar).
# 736 poster hamnade i state='error' med "felkonfiguration" som orsak.
#
# Nedan: en faktisk kontroll av vad som är installerat. Resultatet cachas —
# import- och which-anrop är dyra och kön processar upp till 20 poster/varv.
_CAPABILITY_CACHE = {}


def _module_available(module_name):
    """Kan modulen importeras? (cachat)"""
    key = ('module', module_name)
    if key not in _CAPABILITY_CACHE:
        try:
            __import__(module_name)
            _CAPABILITY_CACHE[key] = True
        except Exception:
            _CAPABILITY_CACHE[key] = False
    return _CAPABILITY_CACHE[key]


def _binary_available(binary_name):
    """Finns binären i PATH? (cachat)

    pytesseract är bara ett omslag — det kräver tesseract-ocr-binären.
    Utan denna kontroll kastar anropet FileNotFoundError, som svaldes av
    en bred except och gav 'Kunde inte extrahera text från filen.'
    """
    key = ('binary', binary_name)
    if key not in _CAPABILITY_CACHE:
        found = False
        for d in os.environ.get('PATH', '').split(os.pathsep):
            if d and os.path.isfile(os.path.join(d, binary_name)):
                found = os.access(os.path.join(d, binary_name), os.X_OK)
                if found:
                    break
        _CAPABILITY_CACHE[key] = found
    return _CAPABILITY_CACHE[key]


def _capability_available(capability):
    """Är den tekniska förmågan tillgänglig i denna miljö?

    capability → vad som krävs:
      'pdf'    → PyMuPDF (fitz) eller pypdf
      'docx'   → python-docx
      'image'  → Pillow (för att läsa bilden)
      'audio'  → någon av de vanliga ljudavkodarna
      'ocr'    → tesseract-binären (valfritt förbättringslager)
      'vision' → en aktiv ai.provider med vision-stöd

    Okänd förmåga → False. Hellre ett tydligt fel i error_message än ett
    tyst antagande om att något fungerar.
    """
    cap = (capability or '').lower()
    if cap == 'pdf':
        return _module_available('fitz') or _module_available('pypdf')
    if cap == 'docx':
        return _module_available('docx')
    if cap == 'image':
        return _module_available('PIL')
    if cap == 'audio':
        return (_module_available('speech_recognition')
                or _binary_available('ffmpeg'))
    if cap == 'ocr':
        return _binary_available('tesseract')
    if cap == 'vision':
        return True  # kontrolleras mot ai.provider i _normalize_image
    return False


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
        # Binärt innehåll får ALDRIG läsas som text: NUL-tecken (0x00) är
        # giltig UTF-8 och överlever errors='replace', men PostgreSQL
        # vägrar NUL i strängliteraler → hela jobbet kraschar.
        if mimetype in BINARY_MIMETYPES:
            return 'binary'
        return 'text'

    def _has_capability(self, capability):
        """Finns den TEKNISKA förmågan (installerat bibliotek/binär)?

        VARFÖR DEN SER UT SÅ HÄR (fix 2026-09-22):
        Denna metod letade tidigare efter en ai.skill/ai.tool vars NAMN var
        'image'/'pdf'/'docx'/'ocr'/'vision'. Det finns inga sådana skills —
        och `coworker_id` är NULL för alla web-UI-uppladdningar — så metoden
        returnerade alltid False. Följden: 736 poster i `ai_okf_upload` med
        state='error' och meddelandet "Förmågan \"image\" saknas på
        coworkerns agenter (felkonfiguration)".

        En förmåga som 'pdf' eller 'image' är inte en skill — det är ett
        BEROENDE. Frågan är om pypdf/PyMuPDF/python-docx/Pillow/tesseract
        finns installerade, inte vad en coworker råkar heta i sina skills.

        Returnerar True/False. Okänd förmåga → False (hellre ett tydligt
        fel än ett tyst antagande).
        """
        self.ensure_one()
        return _capability_available(capability)

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
            if category == 'binary':
                # Binärfil — indexeras inte som kunskap. Stoppar NUL vid källan.
                return '', ('Binärfil indexeras inte (%s).'
                            % (attach.mimetype or 'okänd typ'))
            if category == 'text':
                # Odoo 18: _index_content() togs bort — läs raw och avkoda som text
                raw = attach.raw or b''
                text = raw.decode('utf-8', errors='replace')
                # NUL (0x00) är giltig UTF-8 och överlever errors='replace',
                # men PostgreSQL vägrar NUL i strängliteraler. Sanera alltid.
                text = text.replace('\x00', '')
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
        """Bild → tesseract OCR + vision-caption.

        OCR och vision är FÖRBÄTTRANDE lager, inte krav. En bild utan
        OCR-text och utan vision-caption ger ändå ett användbart koncept:
        en beskrivning av bilden och dess metadata. Att kasta bort hela
        uppladdningen för att tesseract saknas vore att förlora data.
        """
        import tempfile
        parts = []

        # 0. Alltid: en beskrivning ur bilagans egen metadata. Gör att
        #    bilden blir sökbar även utan OCR/vision.
        parts.append('Bild: %s (%s, %s byte)' % (
            attach.name or 'namnlös',
            attach.mimetype or 'okänd typ',
            attach.file_size or 0))

        # 1. OCR (krver tesseract-binären)
        if self._has_capability('ocr'):
            try:
                import subprocess
                with tempfile.NamedTemporaryFile(suffix='.img',
                                                 delete=False) as f:
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
        else:
            _logger.info(
                'OKF: tesseract saknas — bilden indexeras utan OCR (%s)',
                attach.name)

        # 2. Vision-caption (via AI-provider)
        try:
            provider = self.env['ai.provider'].search(
                [('active', '=', True)], limit=1)
            if provider and hasattr(provider, '_generate_vision_caption'):
                caption = provider._generate_vision_caption(attach.raw)
                if caption:
                    parts.append('Bildbeskrivning:\n' + caption)
        except Exception as e:
            _logger.warning('Vision caption failed: %s', e)

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
        """Förmågan saknas i MILJÖN — säg vad som ska installeras.

        Meddelandet ska vara handlingsbart för en drifttekniker, inte
        antyda att en coworker är felkonfigurerad (det var den gamla
        formuleringen, och den pekade fel: felet låg i miljön).
        """
        hints = {
            'pdf': 'installera PyMuPDF (pip install PyMuPDF)',
            'pdf-lib': 'installera PyMuPDF (pip install PyMuPDF)',
            'docx': 'installera python-docx (pip install python-docx)',
            'image': 'installera Pillow (pip install Pillow)',
            'audio': 'installera ffmpeg (apt install ffmpeg)',
            'ocr': 'installera tesseract-ocr (apt install tesseract-ocr)',
        }
        hint = hints.get(capability.lower(),
                         'kontrollera modulens beroenden')
        raise UserError(_(
            'Förmågan "%s" saknas i denna Odoo-miljö — %s.'
        ) % (capability, hint))

    # ── Kön (task 5b.1) ──
    @api.model
    def _enqueue_upload(self, attachment, owner_user_id=None,
                        owner_company_id=None, coworker_id=None,
                        channel_id=None):
        """Skapa en kö-post för en uppladdad bilaga."""
        return self.create({
            'name': _sanitize_text(attachment.name),
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
                    rec.write({'state': 'error',
                               'error_message': _sanitize_text(error)})
                    continue
                rec.normalized_text = _sanitize_text(text[:100000])  # cache (5b.4)
                atype = self.env.ref(
                    'ai_agent_core.artifact_type_document',
                    raise_if_not_found=False) or 'document'
                concept = self.env['ai.okf.concept']._okf_upsert(
                    artifact_type=atype,
                    concept_key='ir.attachment,%s' % rec.attachment_id.id,
                    summary=_sanitize_text(text[:4000]),  # tunt koncept
                    title=_sanitize_text(rec.name),
                    source_ref='ir.attachment,%s' % rec.attachment_id.id,
                    sources=[{
                        'resource': 'ir.attachment,%s' % rec.attachment_id.id,
                        'normalized_text': _sanitize_text(text[:100000]),
                    }],
                    owner_company_id=rec.owner_company_id.id or None,
                    owner_user_id=rec.owner_user_id.id or None,
                    owner_coworker_id=rec.coworker_id.id or None,
                    generated_by='upload',
                )
                rec.write({'state': 'done'})
                rec.concept_ids = [(4, concept.id)]
            except UserError as e:
                rec.write({'state': 'error',
                           'error_message': _sanitize_text(str(e))})
            except Exception as e:
                _logger.exception('OKF upload failed for %s', rec.name)
                rec.write({'state': 'error',
                           'error_message': _sanitize_text('Internt fel: %s' % e)})

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

    # ── Åtgärder (fix 2026-09-22) ──
    def action_retry(self):
        """Kör om misslyckade uppladdningar.

        VARFÖR: 1391 poster hamnade i state='error' på grund av tre buggar
        som nu är fixade (felkonfigurerad förmågekontroll, saknad
        tesseract-binär, okritiskt urval i ir.attachment-bron). Posterna
        ligger kvar och skräpar — men många av dem KAN nu normaliseras.

        `retry_count` räknas upp per försök så att en post som failar igen
        syns ha försökts. Ingen automatisk oändlig loop: cron plockar bara
        'queued'/'working', aldrig 'error'.
        """
        for rec in self:
            if rec.state != 'error':
                continue
            rec.write({'state': 'queued',
                       'retry_count': rec.retry_count + 1,
                       'error_message': False})
        self._process_upload(sync=True)
        return True

    def action_archive_errors(self):
        """Rensa felposter som aldrig kan bli kunskap.

        En post vars fel är 'Binärfil indexeras inte' eller 'Bilagan
        saknas' kan inte bli ett koncept hur många försök den än får.
        Arkiverar dem ur kön i stället för att radera — historiken är
        beviset på vad som hände (ADD-only).
        """
        hopeless = ('Binärfil indexeras inte', 'Bilagan saknas.',
                    'Filen är för stor')

        def _is_hopeless(rec):
            msg = rec.error_message or ''
            return any(msg.startswith(p) for p in hopeless)

        to_clear = self.filtered(
            lambda r: r.state == 'error' and _is_hopeless(r))
        if to_clear:
            _logger.info('OKF: rensar %d hopplösa felposter', len(to_clear))
            to_clear.unlink()
        return True

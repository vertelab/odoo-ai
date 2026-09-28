# -*- coding: utf-8 -*-
"""ai.okf.mixin — generiskt OKF-fältkontrakt.

Vad mixinen är
--------------
OKF (Open Knowledge Format) beskriver kunskap som Markdown med ett enkelt
YAML-huvud: `tags`, `[[länkar]]` och en textkropp. Den här mixinen ger en
Odoo-modell de fälten — och en flagga som säger när de måste genereras om.

Vad mixinen INTE är
-------------------
Den är **inte** en sökmotor (det är `ai.memory.mixin`s halva), och den
känner **ingen domän**. `_okf_body_source()` och `_okf_summary_source()`
implementeras av modellen; mixinen läser aldrig ett fält den inte fick.

    ai.okf.mixin          okf_body · okf_summary · okf_tags · okf_links
                          okf_dirty · okf_indexed_at
         |
         +-- ai.memory.mixin      (ärver, behåller sökhalvan)
         +-- domänbryggorna       (website_ai, hr_ai, crm_ai, ...)

Två fält, inte ett
------------------
    okf_body      HELA kopian av modellens text (description, notes, arch_db)
    okf_summary   DERIVATET — det som embeddas och söks (OKF: description)
    okf_tags      ETIKETTER (OKF: tags) — many2many mot ai.okf.tag
    okf_links     REFERENSER (OKF §6.1) — otypade, '<modell>,<id>'

`summary` går in i `search_vector` och i embeddingen. Ett enda fält som både
källa och summary gör att en 40 000-teckens sida antingen får en skev
`search_vector` eller tappar innehåll. Med två fält är klippningen en
sammanfattning, inte en amputation — och originalet finns kvar.

Flaggan
-------
`okf_dirty` sätts av `create()` och av `write()` när ett fält i
`_okf_dirty_fields()` ändras. Den rensas av `_clear_okf_dirty()` — **direkt i
SQL**, aldrig via `write()`. Att rensa via `write()` tänder hooken igen och
nästa cron-varv (5 min) gör om arbetet: den självåtertändning som gav 38
versioner av `ai.memory,257` innan den fixades 2026-09-21.
"""

import logging
import time

import re

from odoo import api, fields, models
from psycopg2.extras import Json

_logger = logging.getLogger(__name__)

#: Systemparameter för sammanfattningens maxlängd.
#: Default 2000 — samma tal `ai.memory.mixin` redan använde, så att
#: ärvningen är beteende-bevarande.
SUMMARY_MAX_CHARS_PARAM = 'ai_agent_core.okf_summary_max_chars'
SUMMARY_MAX_CHARS_DEFAULT = 2000


class AIOkfMixin(models.AbstractModel):
    """Generiskt OKF-fältkontrakt. Ingen domän, ingen sökmotor."""

    _name = 'ai.okf.mixin'
    _description = 'OKF Mixin — generic knowledge-format fields'
    _auto = False

    # -- Fälten ---------------------------------------------------------
    # Mappningen mot OKF:s markdown-fil:
    #   frontmatter:  title, description (okf_summary), tags (okf_tags)
    #   body:         okf_body  ("everything after the frontmatter")
    #   länkar:       okf_links (OKF §6.1 — koncept till koncept)
    okf_body = fields.Text(
        'OKF Body',
        help='OKF:ns body — modellens hela text (name, description, notes, '
             'arch_db ...). Materialet, inte det som söks. Fylls av '
             'modellens _okf_body_source().')
    okf_summary = fields.Text(
        'OKF Summary',
        help='OKF:ns description — det tunna konceptet som embeddas och '
             'söks. Hålls inom den konfigurerade gränsen '
             '(%s, default %s).' % (SUMMARY_MAX_CHARS_PARAM,
                                    SUMMARY_MAX_CHARS_DEFAULT))
    # OBS: `okf_tags` är INTE deklarerat här.
    #
    # FYND 2026-09-23: en many2many på en ABSTRAKT modell ger SAMMA
    # relationstabell för alla ärvande modeller:
    #
    #   TypeError: Many2many fields ai.company.memory.okf_tags and
    #              ai.personal.memory.okf_tags use the same table
    #
    # Odoo kräver en egen tabell per konkret modell. Fältet deklareras
    # därför av varje modell som bär mixinen:
    #
    #   okf_tags = fields.Many2many(
    #       'ai.okf.tag', '<modell>_okf_tag_rel', 'res_id', 'tag_id')
    #
    # Mixinen äger LÄSNINGEN (_okf_tags_source) och skrivningen
    # (_okf_index_record sätter relationen om fältet finns).
    okf_links = fields.Json(
        'OKF Links', default=list,
        help='OKF:ns [[länkar]] — referenser till andra poster, som '
             '"res.partner,10". Otypade (OKF §6.1): relationen bor i '
             'okf_body, inte i länken. Trasiga länkar är tillåtna — målet '
             'kanske ännu inte har ett koncept.')
    okf_dirty = fields.Boolean(
        'OKF Dirty', default=True, index=True, copy=False,
        help='Satt när OKF-fälten är inaktuella och måste genereras om. '
             'Sätts av create()/write(), rensas av indexeraren.')
    okf_indexed_at = fields.Datetime(
        'OKF Indexed At', readonly=True, copy=False,
        help='När OKF-fälten senast genererades.')

    # ==================================================================
    # Källmetoder — modellen svarar, mixinen läser aldrig själv
    # ==================================================================

    #: Fält som aldrig blir en del av okf_body — oavsett typ.
    #: okf_* är våra egna (cirkulärt), *_search är sökindex,
    #: och Odoo:s tekniska fält bär ingen kunskap.
    OKF_BODY_SKIP = (
        'id', 'display_name', 'create_uid', 'create_date',
        'write_uid', 'write_date', '__last_update',
    )

    def _okf_body_source(self):
        """Modellens text — hela materialet (OKF:s body).

        GENERISK DEFAULT: alla HTML- och Text-fält, plus `name`.
        Modellen överrider när standarden inte räcker (t.ex. website.page,
        där texten bor på `view_id.arch_db`).

        Varför just HTML + Text + name:
          - HTML/Text är där fritexten bor (description, content, notes)
          - `name` bär mest information per tecken och finns på nästan allt
          - CHAR i övrigt utesluts: e-postkopior, sökindex och koder
            (email_cc, phone_mobile_search, referred) är inte innehåll
        """
        self.ensure_one()
        parts = []
        for fname, field in self._fields.items():
            if fname in self.OKF_BODY_SKIP or fname.startswith('okf_'):
                continue
            # Lagrade compute-fält är läsbara; se _okf_links_source.
            if (field.compute or field.related) and not field.store:
                continue
            if fname.endswith('_search'):
                continue
            # Integritet: fält med gruppbegränsning får ALDRIG indexeras.
            # hr.employee bär ssnid, private_email, private_phone m.fl. —
            # att kopiera dem till ett koncept gör dem sökbara för alla.
            if field.groups:
                continue
            if field.type not in ('html', 'text') and fname != 'name':
                continue
            value = self[fname]
            if not value:
                continue
            if field.type == 'html':
                value = self._okf_html_to_text(value)
            value = str(value).strip()
            if value:
                parts.append(value)
        return '\n\n'.join(parts)

    def _okf_summary_source(self):
        """Modellens EGEN sammanfattning, om den har en.

        Returnerar None när modellen inte kan sammanfatta — då tar
        indexerarens fallbackkedja vid (LLM, sedan trunkering).

        Varför den finns trots att en coworker kan sammanfatta: en blog.post
        har redan `subtitle` + `teaser`. Den behöver ingen LLM för att veta
        vad som sammanfattar den, och en deterministisk sammanfattning är
        bättre än en som varierar mellan körningar.
        """
        return None

    def _okf_field_readable(self, fname):
        """Får den här användaren läsa fältet?

        Används av modeller som vill läsa ett gruppbegränsat fält när
        användaren HAR gruppen — den generiska källan hoppar över alla
        gruppbegränsade fält, men en hr-användare ska kunna indexera
        `notes` medan en säljare inte ska det.

        Odoo filtrerar redan värdet vid läsning (self[fname] är tom utan
        gruppen), så detta är ett andra skydd: vi kontrollerar behörigheten
        uttryckligen i stället för att lita på att fältet råkade vara tomt.
        """
        self.ensure_one()
        field = self._fields.get(fname)
        if not field or not field.groups:
            return True
        try:
            return self.user_has_groups(field.groups)
        except Exception:
            return False

    # Modeller som är ETIKETTER men inte har 'tag' i namnet.
    # Odoo kallar dem 'category' eller 'industry'; semantiskt är de taggar:
    # en etikett utan innehåll, kopplad till en post för att gruppera den.
    #
    # FYND 2026-09-24: `res.partner.category` är Odoo:s TAG för partners,
    # men fältet heter `category_id` och målmodellen `category` — regel C
    # missade den helt, och en VIP-kund blev ingen tagg.
    #
    # MEDVETET UTESLUTNA (de ÄR inte taggar):
    #   crm.stage, event.stage, utm.stage, project.project.stage
    #       — etapper är en POSITION i en process, inte en etikett
    #   uom.category — måttenheter
    #   ir.module.category — teknisk klassificering
    OKF_LABEL_MODELS = {
        'res.partner.category',
        'res.partner.industry',
        'hr.employee.category',
        'blog.tag.category',
        'event.tag.category',
        'prd.function_category',
        'prd.requirement_category',
    }

    @staticmethod
    def _okf_is_tag_name(name):
        """Är `name` ett tagg-namn — på ordgräns?

        `tag_ids` → True. `personal_stage_type_ids` → False.
        `project.tags` → True. `project.task.type` → False.

        Utan ordgräns matchar 'tag' inuti 'stage', och varje etappfält
        i Odoo blir en taggkälla.
        """
        for part in re.split(r'[._]', name or ''):
            if part in ('tag', 'tags'):
                return True
        return False

    def _okf_tags_source(self):
        """Modellens taggar (OKF:s `tags:`).

        GENERISK DEFAULT: fält vars namn innehåller 'tag', vars målmodell
        innehåller 'tag', eller vars målmodell står i OKF_LABEL_MODELS.
        Modellen kan överrida.

        En tagg är en ETIKETT — den har inget innehåll att indexera.
        Därför blir den aldrig en länk, och får ingen egen mixin.
        """
        self.ensure_one()
        names = []
        for fname, field in self._fields.items():
            if field.type not in ('many2one', 'many2many'):
                continue
            # Lagrade compute-fält är läsbara; se _okf_links_source.
            if (field.compute or field.related) and not field.store:
                continue
            # Integritet: gruppbegränsade fält indexeras aldrig.
            if field.groups:
                continue
            comodel = field.comodel_name or ''
            # Regel C: fältnamnet, målmodellen, eller etikett-listan.
            #
            # OBS: `'tag' in 'stage'` är True — bokstäverna t-a-g ligger
            # inuti s-t-a-g-e. En naiv delsträngskontroll gjorde därför
            # `personal_stage_type_ids` till en taggkälla, och etapperna
            # ['Inbox', 'Done'] hamnade i okf_tags (mätt 2026-09-24).
            #
            # Vi matchar därför på ORDGRÄNSER: 'tag' eller 'tags' som
            # eget ord i fältnamnet, eller i målmodellens sista led.
            if (not self._okf_is_tag_name(fname)
                    and not self._okf_is_tag_name(comodel)
                    and comodel not in self.OKF_LABEL_MODELS):
                continue
            value = self[fname]
            if not value:
                continue
            if field.type == 'many2one':
                value = value.exists()
            names.extend(n for n in value.mapped('name') if n)
        return names

    def _okf_links_source(self):
        """Modellens länkar (OKF:s [[länkar]]).

        GENERISK DEFAULT: samtliga relationsfält (many2one, many2many,
        one2many) DÄR MÅLETS MODELL BÄR `ai.okf.mixin`.

        Varför bara de med mixinen: en länk är koncept → koncept. En
        referens till en post som aldrig kan bli ett koncept är ingen
        länk — den är brus. (OKF tillåter trasiga länkar, men vi länkar
        bara dit kunskap faktiskt kan uppstå.)

        Formen är `'<modell>,<id>'` — samma som `source_ref`, så en
        konsument kan slå upp målet direkt.

        Regeln är SJÄLVREGISTRERANDE: när partner_ai byggs och lägger
        mixinen på res.partner, blir `crm.lead.partner_id` automatiskt en
        länk. Ingen ändring i crm_ai behövs.
        """
        self.ensure_one()
        refs = []
        for fname, field in self._fields.items():
            if field.type not in ('many2one', 'many2many', 'one2many'):
                continue
            # Ett OBERÄKNAT fält är alltid läsbart. Ett BERÄKNAT fält är
            # läsbart bara om det är lagrat — `project.task.project_id` är
            # compute+store i Odoo 18, och att hoppa över det tappade
            # länken till projektet (mätt 2026-09-23).
            if (field.compute or field.related) and not field.store:
                continue
            # Integritet: en länk till en post användaren inte får se
            # är fortfarande en avslöjad relation.
            if field.groups:
                continue
            comodel = field.comodel_name or ''
            if not self._okf_model_is_indexable(comodel):
                continue
            value = self[fname]
            if not value:
                continue
            refs.extend('%s,%s' % (comodel, rid) for rid in value.ids)
        # Dedup men behåll ordningen (samma post kan nås via flera fält)
        seen = set()
        return [r for r in refs if not (r in seen or seen.add(r))]

    @api.model
    def _okf_model_is_indexable(self, model_name):
        """Bär modellen `ai.okf.mixin`? (cachas per registerladdning)"""
        if model_name not in self.env:
            return False
        inherit = self.env[model_name]._inherit
        if isinstance(inherit, str):
            inherit = [inherit]
        return 'ai.okf.mixin' in inherit

    @staticmethod
    def _okf_html_to_text(html):
        """HTML → text. Använder ai.memory.mixins parser om den finns."""
        if not html:
            return ''
        try:
            from .ai_memory_mixin import AIMemoryMixin
            return AIMemoryMixin._html_to_text(html)
        except Exception:  # noqa: BLE001 — fallback är ett giltigt utfall
            return html

    def _okf_dirty_fields(self):
        """Fält vars ändring gör OKF-fälten inaktuella.

        Default: tom mängd — en modell som inte deklarerar några fält
        flaggas bara av create(). Överridd med modellens egna fältnamn.
        """
        return set()

    # ==================================================================
    # Flaggan — sätts och rensas utanför write()-vägen
    # ==================================================================

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Nya poster ska genereras. `filtered` undviker en skrivning när
        # flaggan redan är satt (default=True).
        stale = records.filtered(lambda r: not r.okf_dirty)
        if stale:
            stale._set_okf_dirty()
        return records

    def write(self, vals):
        """Sätt flaggan när ett innehållsfält ändras — utan AI-arbete.

        Flaggan sätts via `_set_okf_dirty()` (direkt SQL), inte via
        `write()`: att skriva den genom samma hook blir rekursion, och
        ORM:ens buffert kan skriva över den efter vårt UPDATE.
        """
        result = super().write(vals)
        dirty_fields = self._okf_dirty_fields()
        if dirty_fields and (dirty_fields & set(vals)):
            self._set_okf_dirty()
        return result

    def _set_okf_dirty(self):
        """Sätt okf_dirty direkt i SQL — kringgår write()-hooken.

        `flush_recordset()` först: annars kan ORM:ens ännu icke-skrivna
        buffert skrivas EFTER vårt `UPDATE` och skriva över flaggan med det
        gamla värdet. Det var precis vad som hände i testet — flaggan sattes
        och försvann i samma andetag.
        """
        if not self:
            return
        self.flush_recordset(['okf_dirty'])
        self.env.cr.execute(
            'UPDATE %s SET okf_dirty = TRUE WHERE id = ANY(%%s)' % self._table,
            (list(self.ids),))
        self.invalidate_recordset(['okf_dirty'])

    def _clear_okf_dirty(self, extra_vals=None):
        """Rensa flaggan direkt i SQL — spegelbild av `_set_okf_dirty()`.

        Indexeraren skriver `okf_body`/`okf_summary` och rensar flaggan i
        SAMMA anrop. Går den genom `write()` tänder hooken flaggan igen och
        nästa varv (5 min) gör om arbetet — självåtertändningen som gav 38
        versioner av `ai.memory,257` (fixad 2026-09-21).

        Args:
            extra_vals (dict, optional): fält att skriva i samma UPDATE,
                t.ex. `{'okf_body': ..., 'okf_summary': ...}`.
        """
        if not self:
            return
        vals = dict(extra_vals or {})
        vals['okf_dirty'] = False
        vals.setdefault('okf_indexed_at', fields.Datetime.now())
        self.flush_recordset(list(vals))
        # jsonb-fält (okf_links) måste serialiseras — rå SQL går
        # förbi ORM:ens typkonvertering, och psycopg2 tolkar en Python-lista
        # som text[]. psycopg2.extras.Json är samma omslag Odoo själv
        # använder (odoo/models.py importerar det därifrån).
        params = []
        for name, value in vals.items():
            field = self._fields.get(name)
            if field and field.type == 'json' and value is not None:
                params.append(Json(value))
            else:
                params.append(value)
        assignments = ', '.join('%s = %%s' % f for f in vals)
        self.env.cr.execute(
            'UPDATE %s SET %s WHERE id = ANY(%%s)' % (self._table, assignments),
            params + [list(self.ids)],
        )
        self.invalidate_recordset(list(vals))

    # ==================================================================
    # Sammanfattningskedjan
    # ==================================================================

    @api.model
    def _okf_summary_max_chars(self):
        """Gränsen för okf_summary. Läst EN gång per cron-varv av anroparen.

        Default 2000 — samma tal `ai.memory.mixin` använde, så ärvningen är
        beteende-bevarande.
        """
        raw = self.env['ir.config_parameter'].sudo().get_param(
            SUMMARY_MAX_CHARS_PARAM, SUMMARY_MAX_CHARS_DEFAULT)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            _logger.warning(
                'OKF: parametern %s är inte ett tal (%r) — använder %s',
                SUMMARY_MAX_CHARS_PARAM, raw, SUMMARY_MAX_CHARS_DEFAULT)
            return SUMMARY_MAX_CHARS_DEFAULT
        return value if value > 0 else SUMMARY_MAX_CHARS_DEFAULT

    @api.model
    def _okf_summarize_session(self):
        """Cronens delade session för sammanfattningar.

        Skapas en gång och återanvänds. Sessionen är spårbar i loggen (vem
        sammanfattade vad, med vilken token-förbrukning); en session per post
        hade gett 1000 skräpsessioner och gjort felsökning omöjlig — och
        `_write_final_summary()` hade returnerat None för var och en
        (`MIN_SUMMARY_LINES = 4`), så de blev skräp utan värde.
        """
        coworker = self.env.ref('ai_agent_core.coworker_default_assistent',
                                raise_if_not_found=False)
        if not coworker:
            _logger.warning(
                'OKF: coworker_default_assistent saknas — kan inte '
                'sammanfatta långa texter')
            return None
        session = self.env['ai.coworker.session'].sudo().search([
            ('coworker_id', '=', coworker.id),
            ('name', '=', 'OKF-sammanfattning'),
        ], limit=1)
        if not session:
            session = self.env['ai.coworker.session'].sudo().create({
                'coworker_id': coworker.id,
                'name': 'OKF-sammanfattning',
            })
        return session

    def _okf_build_summary(self, text, max_chars, session=None):
        """Fallbackkedjan för sammanfattning.

            1. Modellens egen sammanfattning   (gratis, deterministisk)
            2. Text inom gränsen               (gratis)
            3. Allmän assistent                (LLM)
            4. Trunkering + loggad varning     (sista utväg)

        Returnerar `(summary, source)` där source är 'model' | 'text' |
        'coworker' | 'truncated'.
        """
        self.ensure_one()
        text = text or ''

        # 1. Modellens egen
        own = self._okf_summary_source()
        if own:
            return own[:max_chars], 'model'

        # 2. Ryms inom gränsen
        if len(text) <= max_chars:
            return text, 'text'

        # 3. Allmän assistent
        summary = self._okf_summarize_with_coworker(text, max_chars, session)
        if summary:
            return summary[:max_chars], 'coworker'

        # 4. Trunkering — sista utväg. Loggas: tyst trunkering är samma
        #    felklass som `except: pass`.
        _logger.warning(
            'OKF: kunde inte sammanfatta %s,%s (%d tecken) — trunkerar vid '
            '%d. Posten får ett ofullständigt koncept.',
            self._name, self.id, len(text), max_chars)
        return text[:max_chars], 'truncated'

    def _okf_summarize_with_coworker(self, text, max_chars, session=None):
        """Anropa Allmän assistent för att sammanfatta. None vid fel."""
        coworker = self.env.ref('ai_agent_core.coworker_default_assistent',
                                raise_if_not_found=False)
        if not coworker:
            return None
        if session is None:
            session = self._okf_summarize_session()
        prompt = (
            'Sammanfatta texten nedan på högst %d tecken.\n'
            'Behåll fakta, namn, siffror och datum. Tappa ingen innebörd.\n'
            'Svara med ENDAST sammanfattningen — ingen inledning, ingen '
            'förklaring, ingen rubrik.\n\n'
            '---\n%s' % (max_chars, text)
        )
        try:
            result = coworker.sudo().run(prompt, session=session)
        except Exception as e:  # noqa: BLE001 — felvägen är ett giltigt utfall
            _logger.warning(
                'OKF: sammanfattning via Allmän assistent misslyckades för '
                '%s,%s: %s', self._name, self.id, e)
            return None
        if not result or not str(result).strip():
            return None
        return str(result).strip()

    # ==================================================================
    # Indexerarens ingång — EN väg, delad av cron och debug-knappen
    # ==================================================================

    def _okf_skip_reason(self):
        """Varför posten inte ska indexeras — eller None.

        Skiljer "tomt för alltid" (ADD-only-minnen: rensa flaggan) från
        "tomt just nu" (webbinnehåll: behåll flaggan och försök igen).
        Default: tomt är "just nu" — behåll flaggan.
        """
        return None

    def _okf_artifact_type(self):
        """Konceptets artefakttyp — överridbar av varje modell.

        Default `'knowledge'`: en modell som inte deklarerar en egen typ
        får den generiska. En bryggmodul registrerar sin egen typ i
        `ai.artifact.type` (med `bridge_module` satt) och returnerar dess
        namn här, så taxonomin går att spåra tillbaka till bryggan.
        """
        return 'knowledge'

    def _okf_concept_key(self):
        """Konceptets stabila nyckel — överridbar.

        Default `'<modell>,<id>'`. Nyckeln MÅSTE vara stabil över
        innehållsändringar: härled den aldrig ur text, längd eller radantal.
        En ändrad nyckel startar en ny kedja i stället för en ny version.
        """
        self.ensure_one()
        return '%s,%s' % (self._name, self.id)

    def _okf_owner_vals(self):
        """Exakt EN ägare. `_okf_upsert` kastar ValidationError på noll/flera."""
        self.ensure_one()
        return {'owner_company_id': self.env.company.id}

    def _okf_index_record(self, max_chars=None, session=None):
        """Generera OKF-fälten och skriv konceptet. EN väg.

        Anropas av cronen OCH av debug-knappen — ingen separat
        implementation finns. Returnerar konceptet, eller None om posten
        hoppades över.
        """
        self.ensure_one()
        if max_chars is None:
            max_chars = self._okf_summary_max_chars()

        body = self._okf_body_source() or ''
        skip = self._okf_skip_reason()

        if skip:
            # "Tomt för alltid" — rensa så posten inte blockerar kön.
            self._clear_okf_dirty({'okf_body': body, 'okf_summary': ''})
            _logger.info('OKF: avför %s,%s (%s)', self._name, self.id, skip)
            return None

        if not body.strip():
            # "Tomt just nu" — behåll flaggan, försök nästa körning.
            # Annars tappar vi poster permanent (D5-fällan: "vägen är byggd"
            # utan att ha sett den köra).
            _logger.debug('OKF: %s,%s har ingen text än — behåller flaggan',
                          self._name, self.id)
            return None

        summary, source = self._okf_build_summary(body, max_chars, session)
        links = self._okf_links_source() or []

        # Fälten skrivs och flaggan rensas i SAMMA SQL-anrop (D3).
        # Taggar sätts separat: de är en many2many och kan inte gå via
        # rå SQL på samma sätt.
        self._clear_okf_dirty({
            'okf_body': body,
            'okf_summary': summary,
            'okf_links': links,
        })

        # Taggar: hitta/skapa ai.okf.tag. Fältet deklareras av modellen
        # (en many2many kan inte ligga på en abstrakt mixin — den ger
        # samma tabell för alla ärvande modeller), och konceptet får dem
        # via _okf_upsert.
        tag_ids = []
        if 'okf_tags' in self._fields:
            tag_names = self._okf_tags_source() or []
            if tag_names:
                Tag = self.env['ai.okf.tag'].sudo()
                for name in tag_names:
                    tag = Tag.search([('name', '=', name)], limit=1)
                    if not tag:
                        tag = Tag.create({'name': name})
                    tag_ids.append(tag.id)
                self.sudo().write({'okf_tags': [(6, 0, tag_ids)]})

        source_ref = '%s,%s' % (self._name, self.id)
        vals = {
            'artifact_type': self._okf_artifact_type(),
            'concept_key': self._okf_concept_key(),
            'summary': summary,
            'title': (self.display_name or source_ref)[:120],
            'source_ref': source_ref,
            'sources': [{'resource': source_ref, 'lang': self.env.lang}],
            'attribution': [{'line': 1, 'source_ref': source_ref}],
            'generated_by': 'cron',
            # D5: källan styr versionen, inte derivatet.
            'source_text': body,
            'okf_tags': tag_ids,
        }
        vals.update(self._okf_owner_vals())

        concept = self.env['ai.okf.concept']._okf_upsert(**vals)
        _logger.info(
            'OKF: indexerade %s (summary-källa: %s, %d -> %d tecken)',
            source_ref, source, len(body), len(summary))
        return concept

    def action_okf_index_now(self):
        """Debug-knapp: kör indexeringen för denna post.

        Anropar SAMMA `_okf_index_record()` som cronen. Ingen tredje
        indexeringsväg finns (dashboardens `_run_okf_index()` är den andra,
        och den tas bort i F2.5).
        """
        self.ensure_one()
        concept = self._okf_index_record()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success' if concept else 'warning',
                'title': 'OKF',
                'message': (
                    'Koncept skapat (version %s).' % concept.version
                    if concept else
                    'Inget koncept skapades — texten är tom eller posten '
                    'avfördes. Se loggen.'),
                'sticky': False,
            },
        }

    # ==================================================================
    # Indexeringslistan — utökningsbar av bryggor
    # ==================================================================

    #: Modeller som ska dirty-indexeras, utöver kärnans egna.
    #: En bryggmodul lägger till sina i sin `_register_hook()`:
    #:
    #:     class WebsitePage(models.Model):
    #:         _inherit = ['website.page', 'ai.okf.mixin']
    #:
    #:         def _register_hook(self):
    #:             res = super()._register_hook()
    #:             self.env['ai.okf.mixin']._okf_register_indexable('website.page')
    #:             return res
    #:
    #: VARFÖR REGISTRERING OCH INTE ÖVERRIDNING: `_okf_indexable_models()`
    #: är `@api.model` på en ABSTRAKT modell. En brygga som ärver mixinen på
    #: `website.page` kan inte påverka vad `ai.okf.mixin._okf_indexable_models()`
    #: returnerar — `@api.model`-uppslagningen sker på den abstrakta modellen,
    #: inte på den ärvande. Mätt i test på luke18 2026-09-22: en överridning
    #: på `website.page` hade ingen verkan på cronen.
    _okf_extra_indexable_models = None  # sätts lazy (klassattribut får inte vara muterbart)

    @api.model
    def _okf_register_indexable(self, model_name):
        """Registrera en modell för dirty-indexering (kallas av bryggor).

        Idempotent. Modellen behöver inte finnas än — listan filtreras mot
        `self.env` när cronen kör.
        """
        if self._okf_extra_indexable_models is None:
            type(self)._okf_extra_indexable_models = []
        if model_name not in self._okf_extra_indexable_models:
            self._okf_extra_indexable_models.append(model_name)
            _logger.info('OKF: registrerade %s för dirty-indexering',
                         model_name)
        return True

    @api.model
    def _okf_indexable_models(self):
        """Modeller som bär mixinen och ska dirty-indexeras.

        Basen returnerar de modeller KÄRNAN äger (legacy-minnena) plus de
        bryggor har registrerat via `_okf_register_indexable()`. Kärnan
        namnger aldrig en domänmodell.

        Modeller som inte finns i miljön hoppas över utan fel.
        """
        models = ['ai.personal.memory', 'ai.company.memory']
        for name in (self._okf_extra_indexable_models or []):
            if name not in models:
                models.append(name)
        return models

    #: Hur länge cronen får arbeta innan den ger tillbaka. Sätts under
    #: Odoos egen gräns (limit_time_real=1200 s) med god marginal, så att
    #: körningen hinner avslutas innan nästa startar.
    #:
    #: VARFÖR EN TIDSBUDGET OCH INTE BARA batch_size: cronen kör var 5:e
    #: minut. Om en körning tar längre tid än så startar nästa medan den
    #: förra arbetar — och då håller två transaktioner ir_cron-låset
    #: samtidigt. Mätt på social 2026-09-23: fyra sessioner "idle in
    #: transaction" i upp till 8 minuter, ir_cron låst 60/60 stickprov.
    OKF_CRON_TIME_BUDGET = 240  # sekunder (4 min < 5 min intervall)

    @api.model
    def _okf_cron_index_dirty(self, batch_size=25, time_budget=None):
        """Lätt cron: plocka upp flaggade poster, generera OKF-fälten.

        Tungt arbete (LLM-sammanfattning, embedding) sker HÄR — aldrig i
        write()-vägen. Flaggan rensas av `_clear_okf_dirty()` efter lyckad
        körning.

        Parametern läses EN gång per varv (D7): 1000 poster ska inte ge
        1000 get_param-anrop. Sessionen skapas EN gång (D6).

        **`batch_size` är ett TAK, inte en kvot.** Tidigare tillämpades det
        per modell — med sex registrerade modeller blev det 6 × 50 = 300
        poster per körning. Nu räknas det mot ett gemensamt tak.

        **`time_budget` (sekunder)** gör att cronen ger tillbaka innan
        nästa körning startar. Utan den kan två körningar överlappa och
        hålla `ir_cron`-låset samtidigt (mätt 2026-09-23).
        """
        if time_budget is None:
            time_budget = self.OKF_CRON_TIME_BUDGET
        deadline = time.monotonic() + time_budget

        max_chars = self._okf_summary_max_chars()
        session = self._okf_summarize_session()
        total = 0
        skipped_for_time = 0

        for model_name in self._okf_indexable_models():
            if model_name not in self.env:
                continue
            if total >= batch_size:
                break
            remaining = batch_size - total
            Model = self.env[model_name].sudo()
            dirty = Model.search(
                [('okf_dirty', '=', True)], limit=remaining,
                order='write_date asc')
            for rec in dirty:
                if time.monotonic() >= deadline:
                    skipped_for_time += 1
                    continue
                try:
                    concept = rec._okf_index_record(
                        max_chars=max_chars, session=session)
                    if concept:
                        total += 1
                except Exception as e:  # noqa: BLE001
                    # Posten förblir dirty och plockas upp nästa varv.
                    # exc_info: ett kraschande resultat får inte se ut som
                    # ett tomt (okf-recall-path §16).
                    _logger.warning(
                        'OKF cron: indexering misslyckades för %s,%s: %s',
                        model_name, rec.id, e, exc_info=True)

        if skipped_for_time:
            _logger.info(
                'OKF cron: tidsbudgeten (%ss) slut efter %s poster — '
                '%s kvar till nästa varv',
                time_budget, total, skipped_for_time)
        return total

    # ==================================================================
    # Debug-menyn — OKF-valet per post (skalbaggen)
    # ==================================================================

    @api.model
    def action_open_okf(self, res_id=None):
        """Öppna OKF-fälten för en post (debug-menyn).

        Anropas från OKF-valet i skalbaggen. Två vägar in:

          - **Frontend** (`okf_debug_menu.js`) anropar metoden via ORM med
            `res_id` explicit. Det är den väg som används — Odoo 18 renderar
            inte server-actions i skalbaggen (mätt på social 2026-09-23:
            servern skickade åtgärden, frontend visade den aldrig).
          - **Kontext** (`active_model`/`active_id`) fungerar fortfarande,
            för en bindbar server-action eller ett manuellt anrop.

        Ingen domän nämns: åtgärden är generisk, anroparen bestämmer posten.
        """
        model = self._name
        if res_id is None:
            model = self.env.context.get('active_model') or model
            res_id = self.env.context.get('active_id')
        # FYND 2026-09-23: `res_id` kommer från JS som en array
        # (`orm.call(model, method, [[resId]])`) och kan bli en sträng.
        # Owl kräver `number | boolean` på FormController → krasch.
        try:
            res_id = int(res_id)
        except (TypeError, ValueError):
            return {'type': 'ir.actions.act_window_close'}
        if not model or res_id <= 0:
            return {'type': 'ir.actions.act_window_close'}
        # Kontexten RENSAS på active_model/active_id: den nya vyn öppnar
        # samma post, och ett kvarvarande active_id pekar på fel modell
        # när anroparen var en annan (t.ex. en server-action på en rad).
        ctx = {k: v for k, v in self.env.context.items()
               if k not in ('active_model', 'active_id', 'active_ids')}
        ctx['okf_debug_view'] = True
        return {
            'type': 'ir.actions.act_window',
            'name': 'OKF: %s,%s' % (model, res_id),
            'res_model': model,
            'res_id': res_id,
            'view_mode': 'form',
            'views': [(self.env.ref(
                'ai_agent_core.view_okf_record_form').id, 'form')],
            'target': 'new',
            'context': ctx,
        }

    def action_okf_show_concepts(self):
        """Visa postens OKF-koncept (från debug-vyn)."""
        self.ensure_one()
        source_ref = '%s,%s' % (self._name, self.id)
        concepts = self.env['ai.okf.concept'].search([
            ('source_ref', '=', source_ref),
        ], order='version desc')
        return {
            'type': 'ir.actions.act_window',
            'name': 'OKF-koncept: %s' % source_ref,
            'res_model': 'ai.okf.concept',
            'view_mode': 'list,form',
            'domain': [('id', 'in', concepts.ids)],
            'context': {'create': False},
            'target': 'current',
        }

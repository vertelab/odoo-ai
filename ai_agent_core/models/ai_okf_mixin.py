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
känner **ingen domän**. `_okf_text_source()` och `_okf_summary_source()`
implementeras av modellen; mixinen läser aldrig ett fält den inte fick.

    ai.okf.mixin          okf_text · okf_summary · okf_tags · okf_links
                          okf_dirty · okf_indexed_at
         |
         +-- ai.memory.mixin      (ärver, behåller sökhalvan)
         +-- domänbryggorna       (website_ai, hr_ai, crm_ai, ...)

Två fält, inte ett
------------------
    okf_text      HELA kopian av modellens text (description, notes, arch_db)
    okf_summary   DERIVATET — det som embeddas och söks

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
    okf_text = fields.Text(
        'OKF Text',
        help='Hela kopian av modellens text (description, notes, arch_db ...). '
             'Materialet — inte det som söks. Fylls av modellens '
             '_okf_text_source().')
    okf_summary = fields.Text(
        'OKF Summary',
        help='Derivatet: det tunna konceptet som embeddas och söks. '
             'Hålls inom den konfigurerade gränsen '
             '(%s, default %s).' % (SUMMARY_MAX_CHARS_PARAM,
                                    SUMMARY_MAX_CHARS_DEFAULT))
    okf_tags = fields.Json(
        'OKF Tags', default=list,
        help='OKF-frontmatterns tags — en lista av strängar.')
    okf_links = fields.Json(
        'OKF Links', default=list,
        help='Länkar till andra koncept (OKF:s [[länkar]]) — en lista av '
             '{"ref": "<modell>,<id>"}.')
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

    def _okf_text_source(self):
        """Modellens text — hela materialet.

        Överridd av varje modell som bär mixinen. Default returnerar tom
        sträng så att en modell utan text inte kraschar.
        """
        return ''

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

    def _okf_tags_source(self):
        """Modellens taggar. Default: inga."""
        return []

    def _okf_links_source(self):
        """Modellens länkar till andra koncept. Default: inga."""
        return []

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

        Indexeraren skriver `okf_text`/`okf_summary` och rensar flaggan i
        SAMMA anrop. Går den genom `write()` tänder hooken flaggan igen och
        nästa varv (5 min) gör om arbetet — självåtertändningen som gav 38
        versioner av `ai.memory,257` (fixad 2026-09-21).

        Args:
            extra_vals (dict, optional): fält att skriva i samma UPDATE,
                t.ex. `{'okf_text': ..., 'okf_summary': ...}`.
        """
        if not self:
            return
        vals = dict(extra_vals or {})
        vals['okf_dirty'] = False
        vals.setdefault('okf_indexed_at', fields.Datetime.now())
        self.flush_recordset(list(vals))
        # jsonb-fält (okf_tags, okf_links) måste serialiseras — rå SQL går
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

        text = self._okf_text_source() or ''
        skip = self._okf_skip_reason()

        if skip:
            # "Tomt för alltid" — rensa så posten inte blockerar kön.
            self._clear_okf_dirty({'okf_text': text, 'okf_summary': ''})
            _logger.info('OKF: avför %s,%s (%s)', self._name, self.id, skip)
            return None

        if not text.strip():
            # "Tomt just nu" — behåll flaggan, försök nästa körning.
            # Annars tappar vi poster permanent (D5-fällan: "vägen är byggd"
            # utan att ha sett den köra).
            _logger.debug('OKF: %s,%s har ingen text än — behåller flaggan',
                          self._name, self.id)
            return None

        summary, source = self._okf_build_summary(text, max_chars, session)
        tags = self.okf_tags or self._okf_tags_source() or []
        links = self.okf_links or self._okf_links_source() or []

        # Fälten skrivs och flaggan rensas i SAMMA SQL-anrop (D3).
        self._clear_okf_dirty({
            'okf_text': text,
            'okf_summary': summary,
            'okf_tags': tags,
            'okf_links': links,
        })

        source_ref = '%s,%s' % (self._name, self.id)
        vals = {
            'artifact_type': 'knowledge',
            'concept_key': source_ref,
            'summary': summary,
            'title': (self.display_name or source_ref)[:120],
            'source_ref': source_ref,
            'sources': [{'resource': source_ref, 'lang': self.env.lang}],
            'attribution': [{'line': 1, 'source_ref': source_ref}],
            'generated_by': 'cron',
            # D5: källan styr versionen, inte derivatet.
            'source_text': text,
        }
        vals.update(self._okf_owner_vals())

        concept = self.env['ai.okf.concept']._okf_upsert(**vals)
        _logger.info(
            'OKF: indexerade %s (summary-källa: %s, %d -> %d tecken)',
            source_ref, source, len(text), len(summary))
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

    @api.model
    def _okf_cron_index_dirty(self, batch_size=50):
        """Lätt cron: plocka upp flaggade poster, generera OKF-fälten.

        Tungt arbete (LLM-sammanfattning, embedding) sker HÄR — aldrig i
        write()-vägen. Flaggan rensas av `_clear_okf_dirty()` efter lyckad
        körning.

        Parametern läses EN gång per varv (D7): 1000 poster ska inte ge
        1000 get_param-anrop. Sessionen skapas EN gång (D6).
        """
        max_chars = self._okf_summary_max_chars()
        session = self._okf_summarize_session()
        total = 0
        for model_name in self._okf_indexable_models():
            if model_name not in self.env:
                continue
            Model = self.env[model_name].sudo()
            dirty = Model.search(
                [('okf_dirty', '=', True)], limit=batch_size,
                order='write_date asc')
            for rec in dirty:
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
        return total

    # ==================================================================
    # Debug-menyn — OKF-valet per post (skalbaggen)
    # ==================================================================

    @api.model
    def action_open_okf(self):
        """Öppna OKF-fälten för den aktuella posten (debug-menyn).

        Bindbar server action — läggs på varje modell som bär mixinen via
        `binding_model_id` + `groups_id = base.group_no_one` (debug-läget),
        samma mekanism som *Meta data* och *Data*.

        Läser `active_model`/`active_id` ur kontexten (mönstret från
        `ai.coworker.action_ask_ai_about_record`) och öppnar posten i en
        skrivskyddad vy med bara okf_*-fälten.

        Ingen domän nämns: åtgärden är generisk, bindningen är per modell.
        """
        model = self.env.context.get('active_model')
        res_id = self.env.context.get('active_id')
        if not model or not res_id:
            return {'type': 'ir.actions.act_window_close'}
        return {
            'type': 'ir.actions.act_window',
            'name': 'OKF: %s,%s' % (model, res_id),
            'res_model': model,
            'res_id': res_id,
            'view_mode': 'form',
            'views': [(self.env.ref(
                'ai_agent_core.view_okf_record_form').id, 'form')],
            'target': 'new',
            'context': dict(self.env.context, okf_debug_view=True),
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

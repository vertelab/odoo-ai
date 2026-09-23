# -*- coding: utf-8 -*-
"""ai.okf.concept — OKF-konceptlagret (Open Knowledge Format).

Tunn concept-modell: metadata + summary + source_ref — aldrig innehållskopior.
Per-rad attribution möjliggör exakt injektion per användares rättigheter.
Trigger-modell: dirty-flag → cron → dashboard, alla via _okf_upsert().
"""

import json
import logging
from datetime import datetime, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

from ..fields.pg_vector import PgVector

_logger = logging.getLogger(__name__)

# PostgreSQL-vektorlängd (standardiserad — beslut: text-embedding-3-small @ 1024d)
EMBEDDING_DIM = 1024


class AIOkfConcept(models.Model):
    _name = 'ai.okf.concept'
    _description = 'OKF Concept'
    _order = 'scope, concept_key, version desc'
    _rec_name = 'title'

    def _auto_init(self):
        """Skapa OKF SQL-hjälpfunktioner idempotent vid varje modulinladdning.

        Migration 1.12 skapar dem också, men checkmodule kör --init och
        migrations körs inte — därför behövs CREATE OR REPLACE här (test
        test_okf_can_read_sql). SECURITY DEFINER + bara anrop via
        autentiserade Odoo-metoder.
        """
        res = super()._auto_init()
        self._create_okf_sql_functions()
        return res

    @api.model
    def _create_okf_sql_functions(self):
        """Idempotent: CREATE OR REPLACE ai_okf_can_read + ai_okf_is_follower."""
        cr = self.env.cr
        cr.execute("""
            CREATE OR REPLACE FUNCTION ai_okf_can_read(p_user_id integer, p_model text)
            RETURNS boolean
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public
            AS $$
            DECLARE
                v_model_id integer;
                v_count integer;
            BEGIN
                SELECT id INTO v_model_id FROM ir_model WHERE model = p_model;
                IF v_model_id IS NULL THEN
                    RETURN FALSE;
                END IF;
                SELECT COUNT(*) INTO v_count
                FROM ir_model_access a
                WHERE a.model_id = v_model_id
                  AND a.perm_read = TRUE
                  AND (a.group_id IS NULL OR EXISTS (
                      SELECT 1 FROM res_groups_users_rel g
                      WHERE g.gid = a.group_id AND g.uid = p_user_id
                  ));
                RETURN v_count > 0;
            END;
            $$;
        """)
        cr.execute("""
            CREATE OR REPLACE FUNCTION ai_okf_is_follower(p_user_id integer, p_model text, p_res_id integer)
            RETURNS boolean
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = public
            AS $$
            DECLARE
                v_partner_id integer;
                v_count integer;
            BEGIN
                SELECT partner_id INTO v_partner_id FROM res_users WHERE id = p_user_id;
                IF v_partner_id IS NULL THEN
                    RETURN FALSE;
                END IF;
                SELECT COUNT(*) INTO v_count
                FROM mail_followers f
                WHERE f.res_model = p_model
                  AND f.res_id = p_res_id
                  AND f.partner_id = v_partner_id;
                RETURN v_count > 0;
            END;
            $$;
        """)

    # ── Tre ägar-scopes (exakt en ska vara satt) ──
    owner_company_id = fields.Many2one('res.company', string='Company')
    owner_user_id = fields.Many2one('res.users', string='User')
    owner_coworker_id = fields.Many2one('ai.coworker', string='Coworker')

    scope = fields.Selection([
        ('company', 'Company'),
        ('personal', 'Personal'),
        ('coworker', 'Coworker'),
    ], string='Scope', required=True,
        help='Härlett från ägarfältet. Unik per (scope, concept_key).')

    # ── Artefakttyp + kind ──
    artifact_type_id = fields.Many2one(
        'ai.artifact.type', string='Artifact Type', required=True,
        help='Bär med sig kind (memory|knowledge) som styr beteendet.')

    # ── Versionshantering (ADD-only, beslut 10) ──
    concept_key = fields.Char(
        string='Concept Key', required=True,
        help='Grupperar versioner av samma koncept, t.ex. res.partner,42. '
             'Unik inom scope.')
    version = fields.Integer(string='Version', default=1)
    supersedes_id = fields.Many2one('ai.okf.concept', string='Supersedes')
    superseded_by_id = fields.Many2one('ai.okf.concept', string='Superseded By')

    # ── Innehåll ──
    title = fields.Char(string='Title')
    summary = fields.Text(
        string='Summary',
        help='Markdown summary — det tunna konceptet (ingen innehållskopia).')
    attribution = fields.Json(
        string='Attribution',
        help='Per-rad källattribution: [{"line": 1, "source_ref": "res.partner,42"}, ...]')
    okf_tags = fields.Many2many(
        'ai.okf.tag', 'ai_okf_concept_tag_rel', 'concept_id', 'tag_id',
        string='Tags',
        help='OKF-frontmatterns tags — etiketter, inte länkar. En tagg har '
             'inget innehåll att indexera och får därför ingen egen mixin.')
    source_text = fields.Text(
        'Source Text',
        help='Källtexten som summary härleddes ur (okf-mixin D5). Styr '
             'versionsbeslutet: en LLM som formulerar om samma text ger '
             'ingen ny version, men en faktisk källändring gör det.')
    source_ref = fields.Char(
        string='Source Ref',
        help='Primary source reference, t.ex. res.partner,42')
    sources = fields.Json(
        string='Sources',
        help='OKF frontmatter: [{"resource": "res.partner,42", "last_modified": "...", "usage_count": 3}, ...]')

    # ── Metadata (OKF) ──
    generated = fields.Json(
        string='Generated',
        help='{"by": "cron_sync_graph|process|ai", "at": "..."}')
    verified = fields.Json(
        string='Verified',
        help='{"by": "process|human:<user>", "at": "..."} (derived, not manual)')
    status = fields.Selection([
        ('draft', 'Draft'),
        ('stable', 'Stable'),
        ('superseded', 'Superseded'),
        ('deprecated', 'Deprecated'),
    ], string='Status', default='stable',
        help='superseded = äldre version av ett koncept')

    # ── Livscykel ──
    stale_after = fields.Datetime(
        string='Stale After',
        help='Absolute freshness deadline (OKF, not TTL). Kontrollerar '
             'injektion, inte existens.')
    retention_purpose = fields.Selection([
        ('accounting', 'Accounting'),
        ('tax', 'Tax'),
        ('crm_lead', 'CRM Lead'),
        ('employment', 'Employment'),
        ('marketing', 'Marketing'),
        ('none', 'None'),
    ], string='Retention Purpose', default='none')
    retention_end = fields.Datetime(
        string='Retention End',
        help='Beräknad från retention purpose. Fältet skapas men är passivt '
             'tills GDPR-modulen implementeras (SENARELAGT).')
    archived = fields.Boolean(
        string='Archived', default=False,
        help='Global soft-delete — tas bort från ALL sökning + injektion.')

    # ── Workspace inbox / PARA (D2) ──
    # ADD-only-konceptet skrivs aldrig — inbox-placering sker via
    # workspace.para.ref som pekar PÅ detta koncept (referens, ej kopia).
    para_ref_ids = fields.One2many(
        'workspace.para.ref', 'concept_id', string='PARA References',
        help='Workspace PARA-refs that point at this concept.')
    in_inbox = fields.Boolean(
        string='In Inbox', compute='_compute_in_inbox', search='_search_in_inbox',
        help='True when the concept is owned by a user, not archived, and has '
             'no PARA reference yet (i.e. it is unorganized capture material).')

    # ── Vektorer (pushdown, beslut 11) ──
    embedding = PgVector(
        string='Embedding', dimension=EMBEDDING_DIM,
        help='pgvector(%d) embedding (text-embedding-3-small @ 1024d). '
             'Kolumnen är vector(%d)-typ — satt av hooks.py och migration '
             '18.0.1.202 (migration 1.11 misslyckades tyst: kolumnen var '
             'dimensionslös `vector`, vilket blockerade ivfflat-indexet).'
             % (EMBEDDING_DIM, EMBEDDING_DIM))
    # search_vector — GENERATED COLUMN, skapad av SQL (hooks.py:
    # okf_ensure_search_infrastructure, körs i post_init_hook + migration
    # 18.0.1.202). Är avsiktligt INTE ett ORM-fält: en genererad kolumn
    # ska inte kunna glömmas bort av skrivsidan.
    #   to_tsvector('swedish', coalesce(summary,'') || ' ' || coalesce(title,''))
    # Verifierat i drift 2026-09-13: 110/110 koncept har en icke-tom vektor,
    # svensk stemming fungerar ('kund'/'kunder'/'kunden' → samma 44 träffar).
    entities = fields.Json(
        string='Entities',
        help='Extracted entities for entity linking.')

    # ── Trigger-modell ──
    dirty = fields.Boolean(
        string='Dirty', default=False,
        help='Sätts av write()-hooks på källmodeller; cron plockar upp och '
             'rensar efter _okf_upsert().')

    embedding_state = fields.Selection(
        selection=[('pending', 'Pending'),
                   ('ready', 'Ready'),
                   ('failed', 'Failed'),
                   ('skipped', 'Skipped')],
        string='Embedding State', default='pending', index=True,
        help='Hur det gick att skapa konceptets vektor. '
             'pending = ingen provider/nyckel stund; cron fyller på. '
             'failed = providern svarade men vektorn var felaktig '
             '(fel dimension) — kräver åtgärd. '
             'skipped = ingen text att vektorisera. '
             'Behövs för att efterfyllnad ska veta vad som saknas — '
             'utan markering ser en tom kolumn likadan ut oavsett orsak.')

    _sql_constraints = [
        # Unik per (scope, concept_key, version) — versioner delar
        # concept_key men skiljs åt av version (beslut 15 + 10).
        # Före detta var constraintet (scope, concept_key) vilket
        # blockerade versionshanteringen helt (bugg hittad av tester 9.1).
        ('concept_key_scope_version_uniq',
         'UNIQUE(scope, concept_key, version)',
         'Concept key must be unique within scope and version.'),
    ]

    @api.constrains('owner_company_id', 'owner_user_id', 'owner_coworker_id')
    def _check_exactly_one_owner(self):
        """Exakt en av de tre ägarfälten ska vara satt."""
        for rec in self:
            owners = sum(1 for f in ('owner_company_id', 'owner_user_id',
                                     'owner_coworker_id') if rec[f])
            if owners != 1:
                raise ValidationError(
                    _('Exactly one owner scope must be set (company, user or '
                      'coworker). Got %d.') % owners)

    @api.constrains('scope', 'owner_company_id', 'owner_user_id',
                    'owner_coworker_id')
    def _check_scope_matches_owner(self):
        """Scope måste matcha ägarfältet."""
        for rec in self:
            expected = None
            if rec.owner_company_id:
                expected = 'company'
            elif rec.owner_user_id:
                expected = 'personal'
            elif rec.owner_coworker_id:
                expected = 'coworker'
            if expected and rec.scope != expected:
                raise ValidationError(
                    _('Scope %s does not match owner field (expected %s).')
                    % (rec.scope, expected))

    def write(self, vals):
        """ADD-only: concept-rader är immutabla (beslut 10).

        Endast livscykelfält får ändras: status (superseded av
        _okf_upsert), archived (offboarding), verified (cron/process),
        dirty (trigger-modellen), embedding_state och embedding
        (efterfyllnaden). Allt INNEHÅLL är låst.

        `embedding_state` är ett livscykelfält och inte innehåll: det säger
        vad som hänt med raden, inte vad raden betyder.

        `embedding` RÄKNAS OCKSÅ SOM LIVSCYKEL (rättat 2026-09-22).
        Den tidigare regeln — "vektorn är innehåll, efterfyllnaden skapar en
        ny version" — var en återvändsgränd i drift:

          Efterfyllnaden anropar `_okf_upsert` med SAMMA summary som den
          gamla raden. Då slår `_version_is_unchanged` till och returnerar
          den befintliga raden UTAN att skapa en version. Ingen ny rad, och
          därför ingen `existing.write({'status': 'superseded'})` — den
          gamla raden förblev 'pending' för evigt. Efterfyllnaden plockade
          samma 20 koncept varje körning (order='id asc'), loggade
          "20 av 20 koncept fick vektor" och gjorde ingenting.

        Bevis i drift: `user.140.role` hade 44 versioner där v44 var 'ready'
        och v1–v42 låg kvar som 'pending' + 'stable'. Kön stod still på 449
        medan cronen rapporterade framsteg var 30:e sekund.

        En vektor är HÄRLEDD ur texten — den beskriver inte konceptet, den
        är ett index över det. Att fylla i den ändrar inte vad raden betyder,
        och att tvinga fram en ny version för den skapade bara en oändlig
        svans av dubbletter. `summary`/`title`/`source_ref` är fortfarande
        låsta; det är de som bär innebörden.
        """
        allowed = {'status', 'archived', 'verified', 'dirty',
                   'embedding_state', 'embedding'}
        forbidden = set(vals) - allowed
        if forbidden:
            raise ValidationError(
                _('ai.okf.concept rows are ADD-only (immutable). '
                  'Cannot write fields: %s') % ', '.join(sorted(forbidden)))
        return super().write(vals)

    # ════════════════════════════════════════════
    # _okf_upsert() — konventionen (task 2.4/5.4)
    # ════════════════════════════════════════════
    # ════════════════════════════════════════════
    # Personliga minneskällor (agent-memory-governance 5.x)
    # HR-befattning + personliga mål → OKF personal-koncept
    # ════════════════════════════════════════════

    @api.model
    def _index_user_role(self, user_id):
        """HR-indexerare: hr.employee.job_id → OKF personal (roll)."""
        user = self.env['res.users'].browse(user_id)
        if not user.exists():
            return 0
        try:
            emp = self.env['hr.employee'].search([
                ('work_email', '=', user.login),
            ], limit=1)
            if not emp and user.partner_id:
                emp = self.env['hr.employee'].search([
                    ('work_contact_id', '=', user.partner_id.id)], limit=1)
            if not emp or not emp.job_id:
                return 0
            dept = emp.department_id.name or ''
            summary = f"{user.name} är {emp.job_id.name}"
            if dept:
                summary += f" på avdelningen {dept}"
            self._okf_upsert(
                'roll',
                concept_key=f'user.{user.id}.role',
                summary=summary,
                title=f'Roll: {emp.job_id.name}',
                source_ref=f'hr.employee,{emp.id}',
                attribution=[{'source': f'hr.employee,{emp.id}', 'role': 'hr'}],
                owner_user_id=user.id,
                generated_by='hr_indexer',
            )
            return 1
        except Exception as e:
            _logger.warning('HR-indexerare misslyckades för %s: %s', user_id, e)
            return 0

    @api.model
    def _index_user_goals(self, user_id):
        """Mål-indexerare: ai.personal.goal → OKF personal (mål)."""
        user = self.env['res.users'].browse(user_id)
        if not user.exists():
            return 0
        goals = self.env['ai.personal.goal'].search([
            ('user_id', '=', user.id),
            ('active', '=', True),
        ])
        count = 0
        for goal in goals:
            summary = f"Mål: {goal.name}"
            if goal.description:
                summary += f" — {goal.description[:200]}"
            try:
                self._okf_upsert(
                    'mål',
                    concept_key=f'user.{user.id}.goal.{goal.id}',
                    summary=summary,
                    title=goal.name[:80],
                    source_ref=f'ai.personal.goal,{goal.id}',
                    attribution=[{
                        'source': f'ai.personal.goal,{goal.id}',
                        'role': 'goal',
                    }],
                    owner_user_id=user.id,
                    generated_by='goal_indexer',
                )
                count += 1
            except Exception as e:
                _logger.warning('Mål-indexerare misslyckades för mål %s: %s',
                                goal.id, e)
        return count

    @api.model
    def _index_all_personal_sources(self):
        """Cron: indexera roll + mål för alla aktiva användare."""
        users = self.env['res.users'].search(
            [('active', '=', True), ('share', '=', False)])
        roles = goals = 0
        for u in users:
            try:
                roles += self._index_user_role(u.id)
            except Exception:
                pass
            try:
                goals += self._index_user_goals(u.id)
            except Exception:
                pass
        _logger.info('Personliga minneskällor: %d roller, %d mål', roles, goals)
        return {'roles': roles, 'goals': goals}

    def _produce_embedding(self, summary, title=None, explicit=None):
        """Producera (eller validera) embedding för ett koncept (fas 3.1/3.2).

        Returnerar `(vector, state)` där state är:
          'ready'   — vektorn är giltig och kan användas
          'pending' — ingen vektor kunde skapas (ingen provider/nyckel/nät)
          'failed'  — provider svarade men vektorn var felaktig
          'skipped' — ingen text att vektorisera

        Anropas av `_okf_upsert` när `embedding` utelämnas. Skrivsidan ska
        inte behöva komma ihåg vektorn — den ska bara uppstå.
        """
        if explicit is not None:
            # Explicit vektor: validera ändå (fel dimension = tyst korrupt rad).
            # Modellnamnet används bara i loggning — längden kontrolleras mot
            # `dim`. Vi läser providerns fält i stället för den hårdkodade
            # konstanten, så loggen namnger den modell som faktiskt används.
            Prov = self.env['ai.provider']
            ok = Prov._validate_embedding(
                explicit,
                model=Prov._embedding_provider()._effective_embedding_model()
                if Prov._embedding_provider() else Prov.DEFAULT_EMBEDDING_MODEL,
                dim=EMBEDDING_DIM)
            return (explicit, 'ready') if ok else (None, 'failed')

        text = ' '.join(filter(None, [title, summary])).strip()
        if not text:
            return (None, 'skipped')

        provider = self.env['ai.provider']._embedding_provider()
        if not provider:
            _logger.warning(
                'OKF: ingen provider som kan embedda — konceptet sparas utan '
                'vektor '
                '(embedding_state=pending, cron fyller på senare)')
            return (None, 'pending')

        model = provider._effective_embedding_model()
        # Konceptet är ett DOKUMENT som senare ska hittas av en fråga —
        # alltså 'search_document'. Att utelämna input_type får gatewayen
        # att hänga i 45 s och ge tyst None (mätt 2026-09-14).
        vector = provider._get_embedding(
            model=model, input=text, input_type='search_document')
        if not vector:
            _logger.warning(
                'OKF: embedding misslyckades för koncept %r '
                '(provider=%s, modell=%s)',
                self.env.context.get('okf_key', '?'), provider.name, model)
            return (None, 'pending')

        if not provider._validate_embedding(vector, model=model,
                                            dim=EMBEDDING_DIM):
            return (None, 'failed')
        return (vector, 'ready')

    @api.model
    def _version_is_unchanged(self, existing, summary, title, source_ref,
                             attribution, source_text=None):
        """Är den nya datan innehållsligt identisk med senaste versionen?

        Jämför de fält som BESKRIVER konceptet. Avsiktligt UTELÄMNADE:
        `generated`/`verified` (tidsstämplar — de är alltid nya och hade
        gjort varje körning till en 'ändring'), `embedding` (härleds ur
        texten, så samma text ger samma vektor) och `id`/`version`.

        `attribution` jämförs normaliserat (sorterad på source+role) — två
        listor med samma källor i olika ordning är samma attribution, och
        att behandla dem som olika hade återinfört en rad per körning.

        None och '' normaliseras båda till '' innan jämförelsen, så ett
        utelämnat `title` inte ser ut som en ändring mot ett tomt.

        FYND (2026-09-21): metoden är `@api.model` och anropas som
        `self._version_is_unchanged(existing, ...)` — där `self` är MODELLEN
        (oftast ett tomt recordset), inte raden. `self.ensure_one()` kastade
        därför `Expected singleton: ai.okf.concept()` så fort en befintlig
        version hittades, och hela `_okf_upsert` föll. Felet dolde sig bakom
        versionsstormen: cronens try/except loggade bara en varning, så
        varje post förblev dirty och försökte igen var 5:e minut.
        Kontrollen ska gälla den befintliga raden — det är den vi jämför mot.
        """
        existing.ensure_one()

        def _norm_attr(attr):
            rows = attr or []
            out = []
            for row in rows:
                if isinstance(row, dict):
                    out.append((str(row.get('source', '')),
                                str(row.get('role', ''))))
                else:
                    out.append((str(row), ''))
            return sorted(out)

        # D5 (okf-mixin): när källtexten är känd styr DEN versionen — inte
        # derivatet. En LLM som formulerar om samma text ska inte skapa en
        # ny version; en faktisk källändring ska. Utan källtexten (legacy-
        # anropare) jämförs summary som förut.
        if source_text is not None:
            if (existing.source_text or '') != (source_text or ''):
                return False
        elif (existing.summary or '') != (summary or ''):
            return False
        if (existing.title or '') != (title or ''):
            return False
        if (existing.source_ref or '') != (source_ref or ''):
            return False
        if _norm_attr(existing.attribution) != _norm_attr(attribution):
            return False
        return True

    def _okf_upsert(self, artifact_type, concept_key, summary, title=None,
                    attribution=None, source_ref=None, sources=None,
                    owner_company_id=None, owner_user_id=None,
                    owner_coworker_id=None, generated_by='process',
                    status='stable', stale_after=None, entities=None,
                    embedding=None, search_vector=None, source_text=None,
                    force_new_version=False, okf_tags=None, **kwargs):
        """Skapa ny concept eller ny version vid re-index (ADD-only).

        - Memory-koncept (kind=memory): ny rad endast vid genuint ny inlärning
          (kallaren ansvarar för att bara anropa vid nytt); samma concept_key
          med samma version-rad → ny version.
        - Knowledge-koncept: re-index av samma concept_key skapar ny version
          (version+1, supersedes_id → föregående), föregående blir superseded.
        - Rader är immutabla (ADD-only gäller alltid).

        `force_new_version` (okf-mixin): skapa en ny version även när
        innehållet är oförändrat. Används av efterfyllnaden, som lägger till
        en vektor — en genuin ändring av raden som `_version_is_unchanged`
        annars skulle avvisa.

        `source_text` (okf-mixin D5): källtexten som `summary` härleddes ur.
        När den är satt styr DEN versionsbeslutet — en LLM som formulerar om
        samma text ger ingen ny version, men en faktisk källändring gör det.
        Utelämnad → `summary` jämförs som förut (legacy-anropare oförändrade).
        """
        ArtifactType = self.env['ai.artifact.type']
        if isinstance(artifact_type, str):
            # Namn på artefakttyp (t.ex. 'learning', 'knowledge')
            atype = ArtifactType.search([('name', '=', artifact_type)],
                                        limit=1)
            if not atype:
                raise ValidationError(
                    _('Unknown artifact type %r') % artifact_type)
        elif isinstance(artifact_type, models.BaseModel):
            atype = artifact_type
        else:
            atype = ArtifactType.browse(artifact_type)

        # Scope härleds från ägare (beslut 15)
        if owner_company_id:
            scope = 'company'
        elif owner_user_id:
            scope = 'personal'
        elif owner_coworker_id:
            scope = 'coworker'
        else:
            raise ValidationError(
                _('_okf_upsert() requires exactly one owner.'))

        # Existerande senaste version inom (scope, concept_key)
        existing = self.search([
            ('scope', '=', scope),
            ('concept_key', '=', concept_key),
            ('status', '!=', 'superseded'),
        ], order='version desc', limit=1)

        if existing:
            # ── Är detta en GENUIN ny version? (Fas 14) ────────────────
            # Utan denna kontroll skapade varje cron-körning en ny rad även
            # när innehållet var bokstavligen identiskt. Mätt mot `social`
            # 2026-09-14:
            #
            #   personal|user.2.role : 22 rader, 1 unik text
            #   personal|user.6.role : 22 rader, 1 unik text
            #   company|partner,10   : 22 rader, 1 unik text
            #
            # Kedjan såg ut som en historik men var en logg över att cron
            # hade kört. Värre: eftersom `search_vector` genereras ur
            # summary||title blev 22 identiska rader 22 identiska
            # fulltextposter — sökningen fick 22 chanser att hitta samma
            # sak, vilket förvränger rankningen (DISTINCT ON räddade
            # dedupen i fas 12, men bara för att den fanns).
            #
            # Regeln: samma innehåll = ingen ny version. Villkoret är
            # innehållsligt, inte tidsmässigt — `verified`/`generated`
            # uppdateras ändå inte på den gamla raden (ADD-only), så en
            # 'oförändrad' rad är den ärliga representationen.
            if not force_new_version and self._version_is_unchanged(
                    existing, summary, title, source_ref, attribution,
                    source_text=source_text):
                _logger.debug(
                    'OKF: %s|%s oförändrat sedan v%s — ingen ny version '
                    '(annars skapas en rad per cron-körning)',
                    scope, concept_key, existing.version)
                # Returnera den BEFINTLIGA raden. Anroparen (t.ex.
                # _index_user_role) räknar 1 = 'indexerad', vilket är sant:
                # konceptet finns och är aktuellt.
                return existing

            # ADD-only: ny version istället för att skriva över
            vec, vec_state = self._produce_embedding(
                summary, title=title, explicit=embedding)
            vals = {
                'artifact_type_id': atype.id,
                'scope': scope,
                'concept_key': concept_key,
                'version': existing.version + 1,
                'supersedes_id': existing.id,
                'title': title,
                'summary': summary,
                'source_text': source_text,
                'okf_tags': okf_tags or [],
                'attribution': attribution or [],
                'source_ref': source_ref,
                'sources': sources or [],
                'generated': {'by': generated_by, 'at': fields.Datetime.now().isoformat()},
                'verified': {'by': 'process', 'at': fields.Datetime.now().isoformat()},
                'status': status,
                'stale_after': stale_after,
                'entities': entities or [],
                'embedding': vec,
                'embedding_state': vec_state,
                'owner_company_id': owner_company_id,
                'owner_user_id': owner_user_id,
                'owner_coworker_id': owner_coworker_id,
                'retention_purpose': atype.okf_contract.get('retention_purpose', 'none')
                if atype.okf_contract else 'none',
            }
            new = self.create(vals)
            existing.write({'status': 'superseded'})
            return new

        vec, vec_state = self._produce_embedding(
            summary, title=title, explicit=embedding)
        vals = {
            'artifact_type_id': atype.id,
            'scope': scope,
            'concept_key': concept_key,
            'version': 1,
            'supersedes_id': None,
            'title': title,
            'summary': summary,
            'source_text': source_text,
            'okf_tags': okf_tags or [],
            'attribution': attribution or [],
            'source_ref': source_ref,
            'sources': sources or [],
            'generated': {'by': generated_by, 'at': fields.Datetime.now().isoformat()},
            'verified': {'by': 'process', 'at': fields.Datetime.now().isoformat()},
            'status': status,
            'stale_after': stale_after,
            'entities': entities or [],
            'embedding': vec,
            'embedding_state': vec_state,
            'owner_company_id': owner_company_id,
            'owner_user_id': owner_user_id,
            'owner_coworker_id': owner_coworker_id,
            'retention_purpose': atype.okf_contract.get('retention_purpose', 'none')
            if atype.okf_contract else 'none',
        }
        return self.create(vals)

    def _compute_stale_after(self, last_modified=None):
        """Beräkna stale_after från artefakttypens stale_policy (task 8.1).

        - stale_policy='source': stale_after = källans last_modified + ttl
        - stale_policy='fixed': stale_after = now + ttl
        - ttl=0 → None (aldrig stale)
        """
        self.ensure_one()
        atype = self.artifact_type_id
        if not atype:
            return None
        policy, ttl = atype._get_stale_policy()
        if not ttl:
            return None
        base = last_modified or fields.Datetime.now()
        return base + timedelta(days=ttl)

    @api.model
    def _fresh_domain(self, include_stale_searchable=True):
        """Domän för injektion: exkludera stale koncept (task 8.4).

        Stale-koncept exkluderas från injektion men behålls sökbara.
        Inkluderar alltid: icke-arkiverade, senaste versioner.
        """
        now = fields.Datetime.now()
        domain = [
            ('archived', '=', False),
            ('status', '!=', 'superseded'),
        ]
        if not include_stale_searchable:
            domain.extend(['|', ('stale_after', '=', False),
                           ('stale_after', '>', now)])
        return domain

    def _injectable_concepts(self, limit=50):
        """Senaste versioner per (scope, concept_key), friska, ej arkiverade."""
        domain = self._fresh_domain(include_stale_searchable=False)
        latest = self.search(domain, order='create_date desc', limit=limit)
        return latest._latest_per_key()

    # ════════════════════════════════════════════
    # _okf_search() — sammansatt retrieval (task 7.9)
    # ════════════════════════════════════════════
    @api.model
    def _okf_search(self, query, scope=None, artifact_type_ids=None,
                    department_context=None, time_window=None,
                    limit=20, user=None, hybrid=True, semantic_weight=None,
                    **kw):
        """Sammansatt retrieval-pipeline (D9).

        Ersätter den gamla tredelade fallback-kedjan (pgvector → ILIKE →
        create_date desc). Den kedjan hade tre fel som alla syntes i drift:

        1. **"2b. tsvector-hybrid (swedish FTS)" var en lögn i en
           kommentar** — koden var `summary ILIKE '%<hela prompten>%'`.
           En hel prompt matchar aldrig en konceptsammanfattning, så grenen
           var i praktiken död och föll alltid igenom till fallbacken.
        2. **pgvector-grenen dedupade inte.** `return self.browse(rows)`
           returnerade råa rad-id:n. I drift (`social`): 110 rader men bara
           4 unika koncept — `partner,15` har 44 versioner.
        3. **create_date desc var sista utvägen**, vilket betyder att en
           fråga utan träff returnerade de senaste koncepten som om de vore
           relevanta. Tystnaden såg ut som ett svar.

        Nu är det EN fråga. Poängen är en viktad summa av två signaler:

            (1 - (embedding <=> :q)) * w
          + ts_rank(search_vector, plainto_tsquery('swedish', :query)) * (1-w)

        `COALESCE`-en i 12.3 är hela poängen med sammanslagningen: en rad
        utan vektor (Bifrost saknar embedding-modeller, alla 110 är
        `pending`) får ändå sin text-signal. Utan den hade den semantiska
        blockeraren gjort sökningen helt blind i stället för halvblind.

        **Tomt är tomt.** Ingen fallback returnerar "de senaste koncepten"
        när frågan inte matchar — ett tomt resultat är ett ärligt svar, och
        loggas som `info` tillsammans med hur många koncept som fanns i
        scopet. Ett sökresultat som är tomt för att frågan KRASCHADE är
        däremot inte tomt på riktigt: det loggas som `warning` med
        traceback.
        """
        user = user or self.env.user
        domain = self._fresh_domain(include_stale_searchable=False)
        if scope:
            domain.append(('scope', '=', scope))
        if artifact_type_ids:
            domain.append(('artifact_type_id', 'in', artifact_type_ids))
        if time_window:
            domain.append(('write_date', '>=', time_window))

        # Hybrid kräver en fråga. Utan fråga är "senaste" ett ärligt svar.
        if not hybrid or not query or not query.strip():
            results = self.search(domain, order='create_date desc',
                                  limit=limit)
            return results._latest_per_key()

        # 1. Query-embedding. Misslyckas den fortsätter vi med ren BM25 —
        #    det är den ärliga halva som fungerar.
        embedding = None
        provider = self.env['ai.provider']._embedding_provider()
        if provider:
            # Frågan är en SÖKFRÅGA, inte ett dokument. Asymmetriska
            # modeller (som embed-multilingual-v3.0) lägger dem i olika
            # delar av rummet — fel val ger sämre träff, inte ett fel.
            embedding = provider._get_embedding(
                input=query, input_type='search_query')
            if not embedding:
                _logger.warning(
                    'OKF-sökning: ingen query-vektor (provider=%s). '
                    'Endast BM25-signalen bär resultatet (query=%.60s)',
                    provider.name, query)
        else:
            _logger.warning(
                'OKF-sökning utan aktiv ai.provider — endast BM25 '
                '(query=%.60s)', query)

        # 2. Vikten. `semantic_weight` kommer från anroparen; default 0.7.
        #    Är vektorn borta sätts vikten till 0 — annars hade den
        #    semantiska termen blivit konstant och bara skjutit upp alla
        #    rader lika mycket (en vikt utan signal är brus).
        w = 0.7 if semantic_weight is None else float(semantic_weight)
        w = max(0.0, min(1.0, w))
        if not embedding:
            w = 0.0

        # 3. EN fråga. ALL dedup sker i SQL — det var just frånvaron av
        #    dedup här som gjorde att pgvector-vägen returnerade 110 rader.
        #
        # TVÅ fallgropar som kostade en runda var att hitta:
        #
        # a) COALESCE runt vektortermen är inte kosmetik: `NULL <=> vektor`
        #    ger NULL, och NULL + <bm25> = NULL. Utan den hade HELA summan
        #    blivit NULL i exakt det läge som råder i drift idag (ingen
        #    embedding-modell) — och varje rad fått score NULL, vilket
        #    sorteringen tolkar som "lika", dvs. en tyst slumpordning.
        #
        # b) `qvec IS NULL OR ...` i WHERE måste bort. Den var tänkt som
        #    "utan vektor, lita på BM25" — men `IS NULL` är sant för ALLA
        #    rader, så filtret släppte igenom hela tabellen.
        #
        # c) Filtret `score > 0` räcker INTE ensamt. `ts_rank` returnerar
        #    aldrig exakt 0: en rad som inte matchar frågan får ett golv på
        #    ~1e-20 (verifierat i drift), vilket är strikt större än 0.
        #    Utan den explicita @@ -kontrollen nedan slank varje rad i
        #    tabellen igenom med en omätbar poäng — exakt samma tysta
        #    icke-tomma fallback som skulle bort. Filtret och @@-villkoret
        #    hör ihop; det ena utan det andra är en lögn.
        sql = """
            SELECT id, scope, concept_key, version,
                COALESCE(1 - (embedding <=> %(qvec)s::vector), NULL)
                    AS cosine,
                ts_rank(search_vector,
                        plainto_tsquery('swedish', %(q)s)) AS ts_rank,
                (
                    COALESCE(1 - (embedding <=> %(qvec)s::vector), 0) * %(w)s
                  + ts_rank(search_vector,
                            plainto_tsquery('swedish', %(q)s)) * (1 - %(w)s)
                ) AS score
            FROM ai_okf_concept
            WHERE archived = false
              AND status != 'superseded'
              AND (embedding IS NOT NULL
                   OR search_vector @@ plainto_tsquery('swedish', %(q)s))
        """
        params = {'q': query, 'w': w}
        if embedding:
            params['qvec'] = embedding if isinstance(embedding, str) \
                else '[%s]' % ','.join(str(x) for x in embedding)
        else:
            params['qvec'] = None

        if scope:
            sql += ' AND scope = %(scope)s'
            params['scope'] = scope
        if artifact_type_ids:
            sql += ' AND artifact_type_id = ANY(%(atypes)s)'
            params['atypes'] = list(artifact_type_ids)
        if time_window:
            sql += ' AND write_date >= %(tw)s'
            params['tw'] = time_window

        # Dedup i SQL: senaste versionen per (scope, concept_key). Samma
        # princip som _latest_per_key(), men FÖRE limit — annars hade
        # limit=20 kunnat fyllas av 20 versioner av SAMMA koncept.
        sql = """
            WITH ranked AS (
                %s
            ),
            deduped AS (
                SELECT DISTINCT ON (scope, concept_key)
                    id, cosine, ts_rank, score
                FROM ranked
                ORDER BY scope, concept_key, version DESC
            )
            SELECT id, cosine, ts_rank, score FROM deduped
        """ % sql

        # DISTINCT ON kräver att ORDER BY börjar med nycklarna — sorteringen
        # på score sker därför i ett yttre lager.
        #
        # OBS: `score` MÅSTE projiceras genom båda lagren. Första versionen
        # valde bara `id` i det inre lagret och sorterade på `score` i det
        # yttre — en kolumn som då inte fanns. Felet såg ut som "inga
        # träffar", inte som ett SQL-fel, eftersom allt låg i en try/except.
        #
        # ── VÄG 2: tröskeln är PER SIGNAL (§17.4) ──────────────────────
        # Den summerade `score` är inte ett beslutsmått: den blandar en
        # cosine (0…1) med en onormaliserad ts_rank (~0…0.06) under en
        # vikt. Ett filter på summan är därför ett filter på en enhet som
        # inte finns.
        #
        # I stället ställs frågan varje signal kan svara på:
        #
        #   (cosine >= min_cosine)  ELLER  (ts_rank >= min_ts_rank)
        #
        # ELLER, inte OCH — det är hela poängen. En rad som hittas av den
        # ena signalen är en träff; den andra signalen är frånvarande
        # (NULL/0), inte underkänd. Med OCH hade varje rad utan vektor
        # krävt BM25 över tröskeln OCH tvärtom, dvs. bara de få rader där
        # båda är starka — exakt den blindhet väg 2 ska bota.
        #
        # Raden behåller sin `score` (viktad summa) för SORTERING — det är
        # rätt mått att rangordna på. Tröskeln är ett urval, summan en
        # ordning. De två rollerna ska inte blandas ihop, och det var
        # precis det den gamla koden gjorde.
        min_cosine = kw.get('min_cosine')
        min_ts_rank = kw.get('min_ts_rank')

        # ── DEN GEMENSAMMA NÄMNAREN: en rad måste vara RELEVANT ────────
        # Även utan anropartrösklar måste raden kvala in på NÅGON signal.
        #
        # Historik (och en riktig bugg som §18 avtäckte): kandidatfiltret
        # ovan är `embedding IS NOT NULL OR @@`. Så länge INGA rader hade
        # vektor var det ofarligt — `OR @@` var den enda vägen in, och
        # `@@` är ett äkta relevanskriterium. Men i samma stund som raderna
        # FICK vektorer blev `embedding IS NOT NULL` sant för ALLA, och då
        # släppte kandidatfiltret in hela tabellen oavsett fråga.
        #
        # `score > 0` fångade det inte: `embedding <=> q` är alltid ett
        # tal, så `score` är positivt även för en fråga som inte matchar
        # något. Utan grind returnerade sökningen allt — exakt den "tysta
        # icke-tomma fallback" som 12.5 skulle bota, bara med vektorn som
        # ny ursäkt.
        #
        # Därför: finns ingen tröskel, används BRUSGOLVET. Mätt mot den
        # valda modellen (embed-multilingual-v3.0) mot den egna korpusen:
        # nonsens-frågor ger cosine 0.22–0.39 — Cohere-modellen lämnar
        # aldrig ett tal nära 0 för en kort sträng, så `cosine > 0` är
        # inget relevanskriterium alls. En riktig frågas bästa träff ligger
        # 0.48–0.71. Skiljelinjen är ~0.39.
        #
        # Det här är den ENDA ärliga tolkningen av "ingen tröskel":
        # ingen KALIBRERAD tröskel per strategi — men fortfarande ett
        # golv under vilket allt är brus. Annars vore den ogrindade vägen
        # (som `_tool_okf_search` och alla tester använder) den gamla
        # tysta fallbacken i ny kostym.
        #
        # OBS att golvet bara gäller den SEMANTISKA signalen. En äkta
        # BM25-träff passerar alltid, hur svag vektorn än är — det är
        # ELLER-semantiken, och den får inte tappas här. Därför sätts
        # ALLTID båda klausulerna när golvet appliceras; annars hade en
        # rad med `cosine = NULL` (ingen vektor) mötts av ett ensamt
        # `cosine >= 0.39`, som NULL inte kan uppfylla — och en äkta
        # BM25-träff hade försvunnit. Det var precis vad som hände i
        # `test_bm25_alone_carries_result_when_no_embedding` (0 != 1).
        if min_cosine is None and min_ts_rank is None:
            min_cosine = 0.39
            min_ts_rank = 1e-20

        having = []
        if min_cosine is not None:
            params['min_cosine'] = float(min_cosine)
            having.append('cosine >= %(min_cosine)s')
        if min_ts_rank is not None:
            params['min_ts_rank'] = float(min_ts_rank)
            having.append('ts_rank >= %(min_ts_rank)s')
        gate = (' WHERE (' + ' OR '.join(having) + ')') if having else ''

        sql = (
            'SELECT id FROM (' + sql + ') latest' + gate +
            ' ORDER BY score DESC LIMIT %(limit)s')

        try:
            params['limit'] = limit
            self.env.cr.execute(sql, params)
            rows = [r[0] for r in self.env.cr.fetchall()]
        except Exception as e:
            # Logga HELA felet, inte bara meddelandet. En tyst try/except
            # runt en SQL-sträng kostade tre felsökningsrundor i den här
            # fasen: både en saknad `score`-projektion och ett dict+list
            # typfel såg ut som "inga träffar". Ett sökresultat som är tomt
            # för att frågan KRASCHADE är inte samma sak som ett tomt
            # resultat för att inget matchade.
            _logger.warning('OKF hybridsökning misslyckades: %s', e,
                            exc_info=True)
            rows = []

        if rows:
            return self.browse(rows)

        # 12.5: ÄRLIGT TOMT. Ingen create_date desc-fallback — en fråga
        # utan träff ska vara tom, inte returnera de senaste koncepten som
        # om de vore svar.
        in_scope = self.search_count(domain)
        _logger.info(
            'OKF-sökning gav 0 träffar (query=%.60s, scope=%s, %d koncept '
            'i scopet, vektor=%s)', query, scope or '-', in_scope,
            'ja' if embedding else 'nej')
        return self.browse([])


    def _latest_per_key(self):
        """Returnera bara senaste versionen per (scope, concept_key)."""
        latest_ids = self._read_group(
            [('id', 'in', self.ids)],
            ['scope', 'concept_key'],
            ['id:max'],
        )
        ids = []
        for r in latest_ids:
            # Odoo 18 kan returnera dict eller tuple beroende på version
            if isinstance(r, dict):
                ids.append(r['id'])
            else:
                ids.append(r[-1])
        return self.browse(ids)

    # ════════════════════════════════════════════
    # Per-rad attribution (sektion 3)
    # ════════════════════════════════════════════
    @api.model
    def _validate_attribution(self, attribution):
        """Validera attribution-JSON-schemat (task 3.1).

        Schema: [{"line": int, "source_ref": str}, ...]
        - line: 1-baserat radnummer i summary
        - source_ref: t.ex. "res.partner,42"
        Returnerar (ok, error_msg).
        """
        if attribution is None or attribution is False:
            return True, None
        if isinstance(attribution, str):
            # Json-fält kan returnera sträng — normalisera
            try:
                import json
                attribution = json.loads(attribution)
            except (ValueError, TypeError):
                return False, 'attribution must be a JSON list'
        if not isinstance(attribution, list):
            return False, 'attribution must be a list'
        for item in attribution:
            if not isinstance(item, dict):
                return False, 'attribution items must be objects'
            if 'line' not in item or not isinstance(item.get('line'), int):
                return False, 'each attribution item needs an int line'
            if not item.get('source_ref'):
                return False, 'each attribution item needs a source_ref'
        return True, None

    @api.constrains('attribution')
    def _check_attribution(self):
        """Attribution måste följa schemat vid write."""
        for rec in self:
            ok, err = self._validate_attribution(rec.attribution)
            if not ok:
                _logger.warning('OKF attribution invalid: %r (type=%s)',
                                rec.attribution, type(rec.attribution).__name__)
                raise ValidationError(_('Invalid attribution: %s') % err)

    def _filter_attribution(self, visible_source_ids):
        """Returnera bara rader vars källa är synlig (task 3.2).

        visible_source_ids: dict {source_ref: True} för de källreferenser
        användaren har access till (från access-resolvern).

        Returnerar (visible_lines, hidden_count):
        - visible_lines: list av summary-rader (individuella rader)
        - hidden_count: antal rader som dolts
        """
        self.ensure_one()
        if not self.attribution:
            # Ingen attribution → konservativt: behandla som icke-synlig
            # (fallback, task 3.3) — men bara om vi vet att access krävs.
            # Enklast: alla rader med summary är synliga om conceptet
            # i sig är synligt; här returnerar vi summary-rader utan filter.
            return self.summary.split('\n') if self.summary else [], 0

        lines = self.summary.split('\n') if self.summary else []
        visible_lines = []
        hidden = 0
        for item in self.attribution:
            line_no = item.get('line', 0) - 1  # 0-baserat
            src = item.get('source_ref', '')
            if line_no < 0 or line_no >= len(lines):
                # Rad utanför summary → konservativt: dölj
                hidden += 1
                continue
            if visible_source_ids.get(src):
                visible_lines.append(lines[line_no])
            else:
                hidden += 1
        return visible_lines, hidden

    def _filter_attribution_conservative(self, visible_source_ids):
        """Fallback för rader utan tillförlitlig attribution (task 3.3).

        Rader som saknar attribution-entry behandlas konservativt:
        - Om conceptet har attribution och raden inte finns i attributionen
          → exkludera (osäker källa).
        - Om conceptet helt saknar attribution → synligt om conceptet är
          synligt (ingen per-rad-filtrering möjlig).
        """
        self.ensure_one()
        if not self.attribution:
            return self.summary.split('\n') if self.summary else [], 0

        lines = self.summary.split('\n') if self.summary else []
        attributed_lines = {item.get('line') for item in self.attribution}
        visible_lines = []
        hidden = 0
        for idx, line in enumerate(lines):
            line_no = idx + 1
            if line_no not in attributed_lines:
                # Rad utan attribution → konservativt: dölj
                hidden += 1
                continue
            # Hitta källan för denna rad
            src = next(
                (item.get('source_ref') for item in self.attribution
                 if item.get('line') == line_no), '')
            if visible_source_ids.get(src):
                visible_lines.append(line)
            else:
                hidden += 1
        return visible_lines, hidden

    # ════════════════════════════════════════════
    # Access-resolver (sektion 4)
    # ════════════════════════════════════════════
    @api.model
    def _split_source_ref(self, source_ref):
        """Splitta 'res.partner,42' → ('res.partner', 42)."""
        if not source_ref or ',' not in source_ref:
            return None, None
        model, _, rid = source_ref.rpartition(',')
        try:
            return model.strip(), int(rid.strip())
        except (ValueError, TypeError):
            return None, None

    # ════════════════════════════════════════════
    # Workspace inbox / PARA (tasks 3.1-3.6)
    # ════════════════════════════════════════════

    @api.depends('owner_user_id', 'archived', 'para_ref_ids')
    def _compute_in_inbox(self):
        """Inbox = owned, not archived, not yet placed in PARA."""
        for rec in self:
            rec.in_inbox = bool(
                rec.owner_user_id and not rec.archived and not rec.para_ref_ids)

    @api.model
    def _search_in_inbox(self, operator, value):
        """Support domain ['in_inbox', '=', True] in the Inbox view."""
        placed = self.env['workspace.para.ref'].search(
            [('model', '=', 'ai.okf.concept')]).mapped('res_id')
        # owned, not archived, and (not placed OR operator semantics)
        base = [('owner_user_id', '!=', False), ('archived', '=', False)]
        if (operator == '=' and value) or (operator == '!=' and not value):
            if placed:
                return ['&'] + base + [('id', 'not in', placed)]
            return base
        if placed:
            return ['&'] + base + [('id', 'in', placed)]
        return [('id', 'in', [])]

    @api.model
    def create_from_mail(self, subject, body, from_email=None, from_name=None,
                         user=None, eml_data=None, source_ref=None):
        """Create a personal OKF concept from an incoming email (task 3.2).

        - Adress → res.partner (find/create; simple partner if unknown,
          is_company=False)
        - Grafkoppling: source_ref pekar på källan (mail.message-id)
        - eml-bilaga läggs på konceptet (en källa till sanningen)

        Returns the concept record.
        """
        user = user or self.env.user
        ArtifactType = self.env['ai.artifact.type']
        atype = ArtifactType.search([('name', '=', 'mail')], limit=1) or \
            ArtifactType.search([('name', '=', 'knowledge')], limit=1)
        if not atype:
            atype = ArtifactType.create({'name': 'mail'})

        partner = None
        if from_email:
            partner = self.env['res.partner'].search(
                [('email', '=ilike', from_email)], limit=1)
            if not partner:
                partner = self.env['res.partner'].create({
                    'name': from_name or from_email.split('@')[0],
                    'email': from_email,
                    'is_company': False,
                })

        title = subject or '(No Subject)'
        summary = body[:4000] if body else title
        sources = [{'resource': 'res.partner,%d' % partner.id,
                    'last_modified': fields.Datetime.now().isoformat(),
                    'usage_count': 1}] if partner else None

        concept = self._okf_upsert(
            atype, 'mail:%s' % (source_ref or title), summary,
            title=title, source_ref=source_ref or (
                'res.partner,%d' % partner.id if partner else None),
            sources=sources, owner_user_id=user.id,
            generated_by='process', status='draft')

        # eml-bilaga: en källa till sanningen, sparas som ir.attachment
        if eml_data:
            self.env['ir.attachment'].create({
                'name': (subject or 'email')[:120] + '.eml',
                'datas': eml_data,
                'mimetype': 'message/rfc822',
                'res_model': 'ai.okf.concept',
                'res_id': concept.id,
            })
        return concept

    def action_place_in_para(self, container_id):
        """Manuell inbox→PARA-placering (task 3.3).

        Skapar en workspace.para.ref som pekar på konceptet — konceptet
        skrivs aldrig (ADD-only).
        """
        self.ensure_one()
        container = self.env['workspace.para.container'].browse(container_id)
        if not container.exists():
            raise ValidationError(_('Container not found.'))
        existing = self.env['workspace.para.ref'].search([
            ('model', '=', 'ai.okf.concept'),
            ('res_id', '=', self.id),
            ('container_id', '=', container.id),
        ], limit=1)
        if existing:
            return existing
        return self.env['workspace.para.ref'].create({
            'container_id': container.id,
            'model': 'ai.okf.concept',
            'res_id': self.id,
            'concept_id': self.id,
        })

    def action_nudge_para(self):
        """AI-nudging (en gång, task 3.4): föreslår P/A-placering via
        mail.activity på konceptet. R/A (knowledge/archive) placeras
        automatiskt."""
        self.ensure_one()
        # redan nudgead?
        existing = self.env['mail.activity'].search([
            ('res_model', '=', 'ai.okf.concept'),
            ('res_id', '=', self.id),
            ('activity_type_id.name', '=', 'AI Nudge'),
            ('active', '=', True),
        ], limit=1)
        if existing:
            return existing
        # Auto för knowledge → resource-container
        atype = self.artifact_type_id
        if atype.kind == 'knowledge':
            container = self.env['workspace.para.container'].search([
                ('user_id', '=', self.owner_user_id.id),
                ('kind', '=', 'resource'),
            ], limit=1) or self.env['workspace.para.container'].create({
                'user_id': self.owner_user_id.id,
                'name': 'Resources',
                'kind': 'resource',
            })
            return self.action_place_in_para(container.id)
        # Stale/retention → archive
        if self.archived or self.status == 'deprecated':
            container = self.env['workspace.para.container'].search([
                ('user_id', '=', self.owner_user_id.id),
                ('kind', '=', 'archive'),
            ], limit=1) or self.env['workspace.para.container'].create({
                'user_id': self.owner_user_id.id,
                'name': 'Archive',
                'kind': 'archive',
            })
            return self.action_place_in_para(container.id)
        # Omdöme → nudge (en gång, via mail.activity)
        try:
            return self.activity_schedule(
                'mail.mail_activity_data_todo',
                summary='AI: organisera i PARA (projekt/area)?',
                note='Konceptet är i din inbox. Placera i ett projekt eller en '
                     'area för att aktivera aktivitetsförslag.',
                user_id=self.owner_user_id.id or self.env.uid,
            )
        except Exception:
            _logger.warning('PARA nudge activity_schedule failed', exc_info=True)
            return None

    @api.model
    def _resolve_visible_sources(self, concepts, user=None):
        """Resolve access per källa (task 4.2).

        Access-hierarki: ir.access (modellnivå) ∩ ir.rule (recordnivå)
        ∩ resolver-domäner (AND-filter). Odoos egna metoder är sanning.

        Returnerar dict: {concept_id: set(visible_source_refs)}
        och dict: {concept_id: dict(source_ref → bool synlig)}.
        """
        user = user or self.env.user
        # Exekveringskontext (task 4.4): automatisk = konfigurerad user,
        # aldrig systemuser. (Kron-kontexten sätter detta via context.)
        uid = user.id if user.id != self.env.ref('base.public_user').id \
            else self.env.context.get('okf_resolve_user_id', user.id)

        # Samla unika källreferenser per modell
        by_model = {}  # model -> {(res_id, concept_id)}
        for concept in concepts:
            refs = set()
            if concept.source_ref:
                refs.add(concept.source_ref)
            if concept.attribution:
                refs.update(a.get('source_ref') for a in concept.attribution
                            if a.get('source_ref'))
            if concept.sources:
                refs.update(s.get('resource') for s in concept.sources
                            if s.get('resource'))
            for src in refs:
                model, rid = self._split_source_ref(src)
                if model and rid:
                    by_model.setdefault(model, set()).add((rid, concept.id))

        result = {c.id: {} for c in concepts}
        _logger.info('OKF resolve: concepts=%d by_model=%r', len(concepts),
                     {k: sorted(v) for k, v in by_model.items()})

        for model_name, pairs in by_model.items():
            _logger.info('OKF resolve model=%s pairs=%d', model_name,
                         len(pairs))
            Model = self.env.get(model_name)
            if Model is None:
                # env.get() returnerar tom recordset för befintliga modeller
                # (tomma recordsets är falsy i Odoo!) — bara None = saknas
                _logger.info('OKF resolve model=%s NOT LOADED', model_name)
                continue
            # 1. ir.access (modellnivå)
            try:
                can_read = Model.with_user(uid).check_access_rights(
                    'read', raise_exception=False)
            except Exception as e:
                _logger.info('OKF resolve model=%s access-err %r', model_name, e)
                can_read = False
            if not can_read:
                _logger.info('OKF resolve model=%s NO ACCESS', model_name)
                # Inga källor av denna modell är synliga
                for rid, cid in pairs:
                    result[cid]['%s,%s' % (model_name, rid)] = False
                continue

            # 2. ir.rule (recordnivå) via batch-ORM-search
            #    (söken applicerar ir.rule i SQL — Odoos egen översättare)
            rids = sorted({rid for rid, _ in pairs})
            try:
                visible_ids = Model.with_user(uid).search(
                    [('id', 'in', rids)], order='id')
                visible_set = set(visible_ids.ids)
            except Exception as e:
                _logger.warning('OKF batch search failed for %s: %s',
                                model_name, e)
                visible_set = set()

            # 3. Resolver-domäner (AND-filter, aldrig breddare)
            #    sudo(): resolvern är konfiguration — domänen model_id.model
            #    triggar en ir.model-subquery som begränsade användare inte
            #    får läsa (AccessError annars).
            resolver = self.env['ai.access.resolver'].sudo().search([
                ('model_id.model', '=', model_name),
                ('active', '=', True),
            ], limit=1)
            follower_domain = owner_domain = None
            if resolver:
                follower_domain, owner_domain = resolver._get_domains(
                    user.with_user(uid))

            extra_filtered = set()
            if resolver and (follower_domain or owner_domain):
                # Batch-ORM med AND-domäner: synliga = ir.rule-synliga ∩ resolver-domäner
                combined = [('id', 'in', list(visible_set))]
                if follower_domain:
                    combined += follower_domain
                if owner_domain:
                    combined += owner_domain
                try:
                    extra_filtered = set(
                        Model.with_user(uid).search(combined, order='id').ids)
                    _logger.info('OKF resolve combined=%r → %r', combined,
                                 sorted(extra_filtered))
                except Exception as e:
                    _logger.warning('OKF resolver domain failed for %s: %s',
                                    model_name, e)
                    extra_filtered = visible_set

            final_visible = extra_filtered if resolver and (
                follower_domain or owner_domain) else visible_set

            for rid, cid in pairs:
                result[cid]['%s,%s' % (model_name, rid)] = rid in final_visible
            _logger.info('OKF resolve model=%s visible_set=%s final=%s',
                         model_name, sorted(visible_set),
                         sorted(final_visible))

        return result

    def _get_visible_lines(self, visible_map, conservative=True):
        """Returnera synliga summary-rader per concept (task 4.3).

        visible_map: dict {concept_id: {source_ref: bool}}
        upptäckt = union, injektion = per-källa.
        """
        out = {}
        for concept in self:
            vis = visible_map.get(concept.id, {})
            visible_refs = {src for src, ok in vis.items() if ok}
            if conservative:
                lines, hidden = concept._filter_attribution_conservative(
                    visible_refs)
            else:
                lines, hidden = concept._filter_attribution(visible_refs)
            out[concept.id] = {
                'lines': lines,
                'hidden': hidden,
                'any_visible': bool(lines) or (not concept.attribution and
                                               visible_refs),
            }
        return out

    # ── Express: publicera tillbaka (task 6.3, 6.4) ──

    def action_publish_to_company(self, company_id=None):
        """Publicera personligt→company (task 6.3).

        Explicit only. Eftersom koncept är ADD-only skapas en ny
        company-scope-kopia med samma innehåll + attribution — inget
        ägarskifte i raden (raden är oföränderlig).
        """
        self.ensure_one()
        if self.scope != 'personal':
            raise ValidationError(_('Endast personliga koncept kan publiceras.'))
        company = self.env.company if not company_id else \
            self.env['res.company'].browse(company_id)
        existing = self.search([
            ('scope', '=', 'company'),
            ('concept_key', '=', self.concept_key),
            ('owner_company_id', '=', company.id),
        ], limit=1)
        if existing:
            return existing
        return self._okf_upsert(
            self.artifact_type_id, self.concept_key, self.summary,
            title=self.title, attribution=self.attribution,
            source_ref=self.source_ref, sources=self.sources,
            owner_company_id=company.id,
            generated_by='express', status='stable')

    def action_publish_to_channel(self, channel_id, message=None):
        """Publicera till Discuss-kanal (task 6.4)."""
        self.ensure_one()
        channel = self.env['discuss.channel'].browse(channel_id)
        if not channel.exists():
            raise ValidationError(_('Kanalen finns inte.'))
        body = message or self.summary or self.title or ''
        return channel.message_post(body=body, record_name=self.title)

    # ── Distill attribution rendering (task 4.1) ──

    def render_attribution_html(self):
        """Render summary with per-line clickable source links (task 4.1).

        - Rader med känd källa → klickbar länk till källan.
        - Rader utan källa → flaggas som osäker (⚠ uncertain).

        Returns HTML string suitable for widget='html' in the concept form.
        """
        self.ensure_one()
        summary_lines = (self.summary or '').split('\n')
        attrib = self.attribution or []
        # source_ref → line mapping
        by_line = {}
        for item in attrib:
            ln = item.get('line')
            src = item.get('source_ref')
            if ln is not None and src:
                by_line[int(ln)] = src

        html_parts = []
        for idx, line in enumerate(summary_lines, start=1):
            if not line.strip():
                continue
            src = by_line.get(idx)
            if src:
                html_parts.append(
                    f'<p>{line} <a href="#" '
                    f'onclick="return false;" '
                    f'title="Källa: {src}" class="text-muted" '
                    f'style="font-size: 0.8em; text-decoration: underline; '
                    f'cursor: help;">⤴ {src}</a></p>')
            else:
                html_parts.append(
                    f'<p>{line} <span title="Källa saknas — osäker rad" '
                    f'class="text-warning" style="font-size: 0.8em;">'
                    f'⚠ osäker</span></p>')
        if not html_parts:
            html_parts.append('<p><i>Ingen sammanfattning än.</i></p>')
        return '<div class="okf-attribution">' + ''.join(html_parts) + '</div>'

    # ════════════════════════════════════════════
    # ir.rule-pushdown + access-cache (tasks 4.7/4.9)
    # ════════════════════════════════════════════
    @api.model
    def _get_ir_rule_domain(self, model_name, user=None):
        """Odoos effektiva ir.rule-domän för användaren (task 4.7).

        Använder Odoos egen översättare — ingen PL/pgSQL-återskapning av
        check_access_rule.
        """
        user = user or self.env.user
        Model = self.env.get(model_name)
        if not Model:
            return []
        try:
            return self.env['ir.rule']._compute_domain(
                model_name, mode='read')
        except Exception as e:
            _logger.warning('OKF ir.rule domain failed for %s: %s',
                            model_name, e)
            return []

    @api.model
    def _build_sql_where(self, model_name, user=None):
        """Bygg SQL-where från ir.rule via _where_calc (task 4.7).

        Returnerar (where_sql, params) eller (None, []) om ingen domän.
        """
        user = user or self.env.user
        domain = self._get_ir_rule_domain(model_name, user)
        if not domain:
            return None, []
        Model = self.env[model_name]
        try:
            query = Model._where_calc(domain)
            from odoo.osv import expression
            where_clause, params = query.get_sql()
            return where_clause, params
        except Exception as e:
            _logger.warning('OKF _where_calc failed for %s: %s',
                            model_name, e)
            return None, []

    @api.model
    def _invalidate_access_cache(self):
        """Ogiltigförklara access-cachen (task 4.9).

        Anropas via hook på ir.rule/followers/groups-ändringar.
        """
        self.env['ir.config_parameter'].set_param(
            'okf.access_cache_version',
            str(int(self.env['ir.config_parameter'].get_param(
                'okf.access_cache_version', '0')) + 1))
        return True

    @api.model
    def _get_access_cache_version(self):
        """Nuvarande cache-version (ogiltigförklaringsräknare)."""
        return self.env['ir.config_parameter'].get_param(
            'okf.access_cache_version', '0')

    # ════════════════════════════════════════════
    # Legacy-migrering (tasks 6.1–6.5)
    # ════════════════════════════════════════════
    @api.model
    def action_migrate_legacy(self):
        """Migrera legacy-modeller till ai.okf.concept (körs manuellt
        från dashboard eller via migration 1.13 vid uppgradering).

        6.1 company.memory → company-scope
        6.2 personal.memory → personal-scope
        6.3 ai.memory med quest_id → coworker-scope
        6.4 company.memory.category → ai.artifact.type
        6.5 legacy read-only-flagga
        """
        results = []

        # 6.1 — ai.company.memory
        Company = self.env['ai.company.memory'] if \
            'ai.company.memory' in self.env else None
        if Company is not None and hasattr(Company, 'content'):
            n = 0
            for mem in Company.search([]):
                key = 'ai.company.memory,%s' % mem.id
                if self.search_count([('concept_key', '=', key),
                                      ('scope', '=', 'company')]):
                    continue
                atype = None
                if mem.category_id and hasattr(mem.category_id,
                                               'artifact_type_id'):
                    atype = mem.category_id.artifact_type_id
                self._okf_upsert(
                    artifact_type=atype or 'knowledge',
                    concept_key=key,
                    summary=mem.content or '',
                    title=(mem.content or key)[:80],
                    source_ref=key,
                    owner_company_id=mem.company_id.id or self.env.company.id,
                    generated_by='migration',
                )
                n += 1
            results.append('company.memory: %s' % n)

        # 6.2 — ai.personal.memory
        Personal = self.env['ai.personal.memory'] if \
            'ai.personal.memory' in self.env else None
        if Personal is not None and hasattr(Personal, 'content'):
            n = 0
            for mem in Personal.search([]):
                if not mem.user_id:
                    continue
                key = 'ai.personal.memory,%s' % mem.id
                if self.search_count([('concept_key', '=', key),
                                      ('scope', '=', 'personal')]):
                    continue
                self._okf_upsert(
                    artifact_type='learning',
                    concept_key=key,
                    summary=mem.content or '',
                    title=(mem.content or key)[:80],
                    source_ref=key,
                    owner_user_id=mem.user_id.id,
                    generated_by='migration',
                )
                n += 1
            results.append('personal.memory: %s' % n)

        # 6.3 — ai.memory med quest_id → coworker
        Memory = self.env['ai.memory']
        # ai.memory har en FAISS-hjälpmetod som skuggar ORM:ts search —
        # använd base-sökningen via _search för att komma åt ORM:en
        mem_ids = Memory._search([('quest_id', '!=', False)])
        n = 0
        for mid in mem_ids:
            mem = Memory.browse(mid)
            key = 'ai.memory,%s' % mem.id
            if self.search_count([('concept_key', '=', key),
                                  ('scope', '=', 'coworker')]):
                continue
            self._okf_upsert(
                artifact_type=mem.artifact_type_id or 'learning',
                concept_key=key,
                summary=mem.content or '',
                title=mem.name or key,
                source_ref=key,
                owner_coworker_id=mem.quest_id.id,
                generated_by='migration',
            )
            n += 1
        results.append('ai.memory(coworker): %s' % n)

        # 6.4 — ai.company.memory.category → ai.artifact.type
        Category = self.env['ai.company.memory.category'] if \
            'ai.company.memory.category' in self.env else None
        if Category is not None:
            n = 0
            for cat in Category.search([]):
                name = cat.name or cat.category
                existing = self.env['ai.artifact.type'].search(
                    [('name', '=', name)], limit=1)
                if not existing:
                    self.env['ai.artifact.type'].create({
                        'name': name,
                        'kind': 'knowledge',
                        'bridge_module': 'ai_agent_core',
                        'group_ids': [(6, 0, cat.group_ids.ids)]
                        if hasattr(cat, 'group_ids') else [(6, 0, [])],
                    })
                    n += 1
            results.append('categories: %s' % n)

        # 6.5 — legacy read-only
        self.env['ir.config_parameter'].sudo().set_param(
            'okf.legacy_readonly', 'True')
        results.append('legacy: read-only')
        return '; '.join(results)

    # ════════════════════════════════════════════
    # System prompt-injektion från OKF (tasks 7.1/7.4)
    # ════════════════════════════════════════════
    @api.model
    def _okf_build_system_prompt_block(self, scope, owner_id,
                                       query=None, max_chars=2000,
                                       artifact_type_ids=None,
                                       user=None, include_level1=True,
                                       injection_level='summary_and_key',
                                       semantic_weight=None, limit=None,
                                       hybrid=True):
        """Bygg Hermes-kompatibel system prompt-block från ai.okf.concept.

        Nivåordning (task 7.4):
          Level 2 — Management Summary (först, viktigast)
          Level 3 — Strategy (näst)
          Level 1 — Indexerad data via _okf_search (sist)
          Level 0 — Råmaterial (endast full)

        Access via _resolve_visible_sources (ir.access ∩ ir.rule ∩
        resolver-domäner).

        Args:
            scope (str): company|personal|coworker
            owner_id (int): id för ägaren i scope
            query (str, optional): Sökfråga för Level 1
            max_chars (int): Max tecken totalt
            artifact_type_ids (list, optional): Begränsa till artefakttyper
            user (res.users, optional): Access-kontext
            include_level1 (bool): Inkludera indexerad data
            injection_level (str): summary_only|summary_and_key|full
                - summary_only → L2+L3 (komprimerad, ingen L1/L0)
                - summary_and_key → L2+L3+L1 (default)
                - full → L2+L3+L1+L0
            semantic_weight (float, optional): Vikt för vektorsignalen i L1.
                None → `_okf_search`:s default (0.7). Kommer från
                medarbetarens sökstrategi (fas 13.10).
            limit (int, optional): Max antal L1-koncept. None → 10.
            hybrid (bool): Slå samman vektor- och textsignalen (fas 12).

        Returns:
            str: Formatterad block eller tom sträng
        """
        user = user or self.env.user
        domain = [
            ('scope', '=', scope),
            ('archived', '=', False),
            ('status', '!=', 'superseded'),
        ]
        if scope == 'company':
            domain.append(('owner_company_id', '=', owner_id))
        elif scope == 'personal':
            domain.append(('owner_user_id', '=', owner_id))
        elif scope == 'coworker':
            domain.append(('owner_coworker_id', '=', owner_id))
        if artifact_type_ids:
            domain.append(('artifact_type_id', 'in', artifact_type_ids))

        atype_names = {a.name: a.id for a in
                       self.env['ai.artifact.type'].search([])}

        # Level 2 — Management Summary
        mgmt_concepts = self.search(domain + [
            ('concept_key', 'ilike', 'mgmt_summary%'),
        ], order='version desc')
        if not mgmt_concepts:
            # Fallback: artifact type 'mgmt_summary' eller 'strategy'
            mgmt_concepts = self.search(domain + [
                ('artifact_type_id.name', '=', 'mgmt_summary'),
            ], order='version desc')

        parts = []
        injected_ids = []  # för lineage: koncept som faktiskt injiceras
        budget = max_chars
        mgmt_block = self._format_concept_block(
            mgmt_concepts._latest_per_key(), budget // 2, 'MANAGEMENT SUMMARY',
            user=user)
        if mgmt_block:
            parts.append(mgmt_block)
            budget -= len(mgmt_block)
            injected_ids.extend(mgmt_concepts._latest_per_key().ids)

        # Level 3 — Strategy
        strategy_concepts = self.search(domain + [
            ('artifact_type_id.name', 'in', ['strategy', 'knowledge']),
        ], order='version desc', limit=10)
        if not strategy_concepts:
            strategy_concepts = self.search(domain + [
                ('concept_key', 'ilike', 'strategy%'),
            ], order='version desc', limit=10)
        strategy_block = self._format_concept_block(
            strategy_concepts._latest_per_key(), max_chars // 2, 'STRATEGY',
            user=user)
        if strategy_block:
            parts.append(strategy_block)
            injected_ids.extend(strategy_concepts._latest_per_key().ids)

        # Level 1 — Indexerad data via _okf_search
        want_l1 = injection_level in ('summary_and_key', 'full')
        if want_l1 and include_level1 and query:
            search_results = self._okf_search(
                query, scope=scope, artifact_type_ids=artifact_type_ids,
                limit=limit or 10, user=user, hybrid=hybrid,
                semantic_weight=semantic_weight)
            if search_results:
                l1_block = self._format_concept_block(
                    search_results, max_chars // 3, 'RELEVANT KUNSKAP',
                    user=user)
                if l1_block:
                    parts.append(l1_block)
                    injected_ids.extend(search_results.ids)

        # Level 0 — Råmaterial (endast full)
        if injection_level == 'full':
            raw = self.search(domain + [
                ('artifact_type_id.name', '=', 'raw'),
            ], order='create_date desc', limit=3)
            if raw:
                raw_block = self._format_concept_block(
                    raw._latest_per_key(), max_chars // 3, 'RÅMATERIAL',
                    user=user)
                if raw_block:
                    parts.append(raw_block)
                    injected_ids.extend(raw._latest_per_key().ids)

        # Lineage: concept_injected (session → koncept) när sessionen känd
        session_id = self.env.context.get('ai_lineage_session_id')
        if session_id and injected_ids and 'ai.lineage.link' in self.env:
            Lineage = self.env['ai.lineage.link']
            for cid in dict.fromkeys(injected_ids):
                Lineage._add_edge(
                    'concept_injected',
                    f'ai.coworker.session,{session_id}',
                    f'ai.okf.concept,{cid}')

        if not parts:
            return ''
        return '\n\n'.join(parts)

    def _format_concept_block(self, concepts, max_chars, header_label,
                              user=None):
        """Formatera OKF-koncept till prompt-block (access-filtrerad)."""
        if not concepts:
            return ''
        concepts = concepts._latest_per_key()
        visible = self._resolve_visible_sources(concepts, user=user)
        entries = []
        chars = 0
        for c in concepts:
            # Access: om konceptet har källreferenser och inga är synliga → skippa
            refs = []
            if c.source_ref:
                refs.append(c.source_ref)
            if c.attribution:
                refs.extend(a.get('source_ref') for a in c.attribution
                            if a.get('source_ref'))
            if c.sources:
                refs.extend(s.get('resource') for s in c.sources
                            if s.get('resource'))
            if refs:
                vis = visible.get(c.id, {})
                if not any(vis.get(r, True) for r in refs):
                    # Alla källreferenser är osynliga → hoppa över
                    if refs and all(vis.get(r, False) is False for r in refs):
                        continue
            content = c.summary or c.title or ''
            if chars + len(content) > max_chars:
                break
            entries.append(content)
            chars += len(content)

        if not entries:
            return ''
        content = '\n§ '.join(entries)
        pct = min(100, int(chars / max_chars * 100)) if max_chars else 0
        header = f"{header_label} [{pct}% — {chars:,}/{max_chars:,} chars]"
        separator = '═' * 46
        return f"{separator}\n{header}\n{separator}\n{content}"

    @api.model
    def _okf_cron_backfill_embeddings(self, batch_size=20):
        """Efterfyllnad av saknade vektorer (okf-recall-path fas 3.3).

        Flyttad hit från `ai.memory` (okf-mixin, 2026-09-22): den fyller
        vektorer på `ai.okf.concept` och är OKF:s egen efterfyllnad — den
        råkade bara bo på RAG-modellen (därför heter konstanten
        `EMBEDDING_DIM` — samma konstant, samma modul.)

        Plockar koncept vars `embedding_state` inte är 'ready' och försöker
        skapa vektorn. Idempotent: lyckade rader markeras 'ready' och plockas
        aldrig upp igen; misslyckade lämnas i sin markering.

        VARFÖR EN EGEN CRON: koncept skrivna innan embeddings fungerade har
        en tom vektorkolumn. Utan efterfyllnad kräver varje sådan rad en
        manuell åtgärd — och utan `embedding_state` går det inte att skilja
        "aldrig försökt" från "försökt och misslyckats".

        Avsiktligt utan tung logik: tunga saker händer i `_produce_embedding`
        som REDAN körs via `_okf_upsert` på nya koncept. Denna cron räddar
        bara eftersläntrare.
        """
        pending = self.search([
            ('embedding_state', 'in', ('pending', 'failed')),
            ('archived', '=', False),
            ('status', '!=', 'superseded'),
        ], limit=batch_size, order='id asc')

        if not pending:
            return 0

        provider = self.env['ai.provider']._embedding_provider()
        if not provider:
            _logger.warning(
                'OKF efterfyllnad: ingen provider som kan embedda — %s koncept '
                'väntar fortfarande', len(pending))
            return 0

        # Providerns EGNA modell — inte den hårdkodade konstanten.
        #
        # FYND på social 2026-09-23: `DEFAULT_EMBEDDING_MODEL` är
        # 'text-embedding-3-small', som inte finns i Bifrosts modellista.
        # Anropet routades till en trial-nyckel som inte svarar: 3 × 20 sek
        # = 60 sek per koncept. Med 118 pending-koncept blev backfill-cronen
        # två timmar lång — och höll `ir_cron`-låset.
        #
        # Providerns fält (`embedding_model`) är den enda sanningen;
        # `_effective_embedding_model()` läser det med fallback.
        model = provider._effective_embedding_model()
        filled = 0
        for concept in pending:
            text = ' '.join(filter(None, [concept.title, concept.summary])).strip()
            if not text:
                # 'skipped' får skrivas — det är ett livscykelfält. Ingen ny
                # version: det finns inget innehåll att versionera, och en
                # tom kopia vore bara skräp i versionskedjan.
                concept.write({'embedding_state': 'skipped'})
                continue

            vector = provider._get_embedding(
                model=model, input=text, input_type='search_document')
            if not vector:
                # Lämna som pending — nästa körning försöker igen.
                # Vi kan inte märka om raden utan att skapa en ny version,
                # så vi rör den inte alls: 'pending' är redan sanningen.
                continue

            if not provider._validate_embedding(vector, model=model,
                                                dim=EMBEDDING_DIM):
                # Fel dimension: markera 'failed' så den kräver tillsyn.
                concept.write({'embedding_state': 'failed'})
                continue

            # VIKTIGT: koncept-rader är ADD-only (beslut 10). Vektorn kan
            # alltså inte skrivas in i den befintliga raden — efterfyllnaden
            # skapar en NY VERSION via _okf_upsert. Den gamla raden blir
            # 'superseded' och den nya bär vektorn. Immutabiliteten är
            # bevarad: historiken finns kvar, inget skrivs över.
            owner = self._okf_owner_for_concept(concept)
            # `source_text` skickas INTE med flit. Efterfyllnaden lägger
            # till en vektor — det är en genuin ändring av raden, även om
            # sammanfattningen är identisk. Skickas källtexten med hade
            # `_version_is_unchanged` (D5) sett oförändrat innehåll och
            # returnerat den gamla raden UTAN vektor — och efterfyllnaden
            # hade varit verkningslös.
            #
            # Utan `source_text` jämförs `summary` som förut, och eftersom
            # den är identisk... se nästa stycke.
            self._okf_upsert(
                artifact_type=concept.artifact_type_id or 'learning',
                concept_key=concept.concept_key,
                summary=concept.summary,
                title=concept.title,
                source_ref=concept.source_ref,
                entities=concept.entities,
                generated_by='backfill',
                embedding=vector,
                force_new_version=True,
                **owner
            )
            filled += 1

        _logger.info('OKF efterfyllnad: %s av %s koncept fick vektor',
                     filled, len(pending))
        return filled

    @api.model
    def _okf_owner_for_concept(self, concept):
        """Plocka ut ägar-argumenten från ett koncept för _okf_upsert.

        _okf_upsert kräver exakt ett ägarfält — inte ett browse-id.
        """
        if concept.owner_company_id:
            return {'owner_company_id': concept.owner_company_id.id}
        if concept.owner_user_id:
            return {'owner_user_id': concept.owner_user_id.id}
        if concept.owner_coworker_id:
            return {'owner_coworker_id': concept.owner_coworker_id.id}
        return {'owner_company_id': self.env.company.id}

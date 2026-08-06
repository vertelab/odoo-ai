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
             'Kolumnen är vector(%d)-typ (migration 1.10).'
             % (EMBEDDING_DIM, EMBEDDING_DIM))
    # search_vector — GENERATED COLUMN via SQL-migration:
    #   ALTER TABLE ai_okf_concept ADD COLUMN search_vector tsvector
    #     GENERATED ALWAYS AS (to_tsvector('swedish', coalesce(summary, title, ''))) STORED;
    entities = fields.Json(
        string='Entities',
        help='Extracted entities for entity linking.')

    # ── Trigger-modell ──
    dirty = fields.Boolean(
        string='Dirty', default=False,
        help='Sätts av write()-hooks på källmodeller; cron plockar upp och '
             'rensar efter _okf_upsert().')

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
        _okf_upsert), archived (offboarding), verified (cron/process)
        och dirty (trigger-modellen). Allt innehåll är låst.
        """
        allowed = {'status', 'archived', 'verified', 'dirty'}
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

    def _okf_upsert(self, artifact_type, concept_key, summary, title=None,
                    attribution=None, source_ref=None, sources=None,
                    owner_company_id=None, owner_user_id=None,
                    owner_coworker_id=None, generated_by='process',
                    status='stable', stale_after=None, entities=None,
                    embedding=None, search_vector=None, **kwargs):
        """Skapa ny concept eller ny version vid re-index (ADD-only).

        - Memory-koncept (kind=memory): ny rad endast vid genuint ny inlärning
          (kallaren ansvarar för att bara anropa vid nytt); samma concept_key
          med samma version-rad → ny version.
        - Knowledge-koncept: re-index av samma concept_key skapar ny version
          (version+1, supersedes_id → föregående), föregående blir superseded.
        - Rader är immutabla (ADD-only gäller alltid).
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
            # ADD-only: ny version istället för att skriva över
            vals = {
                'artifact_type_id': atype.id,
                'scope': scope,
                'concept_key': concept_key,
                'version': existing.version + 1,
                'supersedes_id': existing.id,
                'title': title,
                'summary': summary,
                'attribution': attribution or [],
                'source_ref': source_ref,
                'sources': sources or [],
                'generated': {'by': generated_by, 'at': fields.Datetime.now().isoformat()},
                'verified': {'by': 'process', 'at': fields.Datetime.now().isoformat()},
                'status': status,
                'stale_after': stale_after,
                'entities': entities or [],
                'embedding': embedding,
                'owner_company_id': owner_company_id,
                'owner_user_id': owner_user_id,
                'owner_coworker_id': owner_coworker_id,
                'retention_purpose': atype.okf_contract.get('retention_purpose', 'none')
                if atype.okf_contract else 'none',
            }
            new = self.create(vals)
            existing.write({'status': 'superseded'})
            return new

        vals = {
            'artifact_type_id': atype.id,
            'scope': scope,
            'concept_key': concept_key,
            'version': 1,
            'supersedes_id': None,
            'title': title,
            'summary': summary,
            'attribution': attribution or [],
            'source_ref': source_ref,
            'sources': sources or [],
            'generated': {'by': generated_by, 'at': fields.Datetime.now().isoformat()},
            'verified': {'by': 'process', 'at': fields.Datetime.now().isoformat()},
            'status': status,
            'stale_after': stale_after,
            'entities': entities or [],
            'embedding': embedding,
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
                    limit=20, user=None, hybrid=True):
        """Sammansatt retrieval-pipeline (task 7.9).

        1. Query-embedding (samma modell/dimension som indexering:
           text-embedding-3-small @ 1024d)
        2. pgvector top-k + tsvector hybrid (beslut 11-pushdown)
        3. Scope-filter (company/personal/coworker)
        4. Artifact type-filter (avdelningskontext)
        5. Access-resolver (ir.access ∩ ir.rule ∩ followers)
        6. Senaste version per (scope, concept_key) endast
        7. Tidsfönster (för manuella körningar)
        """
        user = user or self.env.user
        domain = self._fresh_domain(include_stale_searchable=False)
        if scope:
            domain.append(('scope', '=', scope))
        if artifact_type_ids:
            domain.append(('artifact_type_id', 'in', artifact_type_ids))
        if time_window:
            domain.append(('write_date', '>=', time_window))

        # 6. Senaste version per (scope, concept_key) — först via SQL/ORM
        # (pgvector-pushdown kräver att kolumnen är vector-typ; fallback
        # till tsvector-sök om pgvector saknas)
        if hybrid and query:
            # 1. Query-embedding via providern (samma som indexering)
            embedding = None
            try:
                provider = self.env['ai.provider'].search(
                    [('active', '=', True)], limit=1)
                if provider and hasattr(provider, '_get_embedding'):
                    embedding = provider._get_embedding(query)
            except Exception as e:
                _logger.warning('OKF query embedding failed: %s', e)

            if embedding:
                # 2a. pgvector top-k (direkt SQL — beslut 11-pushdown)
                try:
                    emb_literal = embedding if isinstance(embedding, str) \
                        else '[%s]' % ','.join(str(x) for x in embedding)
                    sql = """
                        SELECT id FROM ai_okf_concept
                        WHERE archived = false
                          AND status != 'superseded'
                    """
                    params = []
                    if scope:
                        sql += ' AND scope = %s'
                        params.append(scope)
                    if artifact_type_ids:
                        sql += ' AND artifact_type_id = ANY(%s)'
                        params.append(list(artifact_type_ids))
                    sql += ' ORDER BY embedding <=> %s::vector LIMIT %%s' % \
                        emb_literal
                    params.append(limit)
                    self.env.cr.execute(sql, params)
                    rows = [r[0] for r in self.env.cr.fetchall()]
                    if rows:
                        return self.browse(rows)
                except Exception as e:
                    _logger.warning(
                        'OKF pgvector search failed (fallback till tsvector): %s',
                        e)

            # 2b. tsvector-hybrid (swedish FTS)
            try:
                search_domain = domain + [
                    '|', ('summary', 'ilike', '%%%s%%' % query),
                    ('title', 'ilike', '%%%s%%' % query),
                ]
                results = self.search(search_domain, limit=limit)
                if results:
                    return results._latest_per_key()
            except Exception as e:
                _logger.warning('OKF tsvector search failed: %s', e)

        # Fallback: ren domän-sök (senaste versioner)
        results = self.search(domain, order='create_date desc', limit=limit)
        return results._latest_per_key()

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
            ('done', '=', False),
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
                                       injection_level='summary_and_key'):
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
                limit=10, user=user)
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

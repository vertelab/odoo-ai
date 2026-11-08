# -*- coding: utf-8 -*-
"""Post-install hook: create Quest Builder and Skill Builder quests."""

import logging
import re

_logger = logging.getLogger(__name__)


def pre_init_hook_check_conflicts(env):
    """Förhindra installation om inkompatibel modul (ai_agent) är installerad.

    ai_agent (legacy) och ai_agent_core definierar samma modeller och xmlids
    (ai.tool, view_ai_tool_tree/view_ai_tool_form, action_ai_tool m.fl.) och
    kan inte köras samtidigt. Odoo 18-kärnan läser inte manifest-nyckeln
    'conflicts', så spärren implementeras som pre_init_hook.
    """
    cr = env.cr
    cr.execute(
        "SELECT state FROM ir_module_module WHERE name = 'ai_agent'")
    row = cr.fetchone()
    if row and row[0] in ('installed', 'to upgrade', 'to remove'):
        _logger.error(
            'ai_agent_core installation blocked: ai_agent is installed (state=%s). The modules are incompatible — uninstall ai_agent before installing ai_agent_core.', row[0])
        from odoo.exceptions import UserError
        raise UserError(
            'ai_agent_core cannot be installed alongside ai_agent.\n\nThey define the same models/views (ai.tool, view_ai_tool_*,\naction_ai_tool) and are incompatible. Uninstall ai_agent first (Apps → ai_agent → Uninstall).'
        )


# ── Delad vektor-kolumnhjälpare ──────────────────────────────────────────
#
# VARFÖR DEN FINNS: `post_init_hook_personal_memory` läste kolumntypen EN
# gång och använde samma värde för att styra två på varandra följande steg:
#
#     row = cr.fetchone()              # 'text' eller 'vector'
#     if row and row[0] != 'vector':
#         ALTER ... TYPE vector(1024)  # kolumnen blir vector(1024)
#     if row and row[0] == 'vector':   # row är FORTFARANDE 'text'
#         CREATE INDEX ...             # körs aldrig i samma anrop
#
# Efter en lyckad konvertering beskriver `row` inte längre verkligheten.
# Nästa uppgradering läser 'vector' (dimensionslös), hoppar över ALTER
# eftersom row[0] == 'vector', och når index-blocket — där CREATE INDEX
# USING ivfflat faller på "column does not have dimensions". Båda grenarna
# var döda för en dimensionslös kolumn: ingen konvertering, inget index.
#
# Mätt i drift (dina, 2026-10-30):
#     ai_personal_memory | vector    ← ingen dimension
#     ai_company_memory  | vector    ← ingen dimension
#     ivfflat-index      | (inga alls)
#
# Rättelsen är att aldrig spara ett "före"-värde: varje steg verifierar
# resultatet mot katalogen EFTER att det kört.

_IDENT_RE = re.compile(r'^[a-z_][a-z0-9_]*$')


def _vector_column_type(cr, table, column):
    """Läs kolumnens faktiska typ som format_type ger den.

    Returnerar t.ex. 'text', 'vector' (dimensionslös) eller 'vector(1024)'.
    `None` om kolumnen inte finns.
    """
    cr.execute("""
        SELECT format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a
        JOIN pg_class c ON a.attrelid = c.oid
        JOIN pg_namespace n ON c.relnamespace = n.oid
        WHERE c.relname = %s AND a.attname = %s
          AND n.nspname = 'public' AND a.attnum > 0 AND NOT a.attisdropped
    """, (table, column))
    row = cr.fetchone()
    return row[0] if row else None


def ensure_vector_column(cr, table, column='embedding', dim=1024):
    """Gör `column` till vector(dim) och säkerställ ivfflat-indexet.

    Idempotent. Läser kolumntypen EFTER varje steg i stället för att spara
    ett "före"-värde — det var den cirkulära logiken som gjorde att varken
    konverteringen eller indexet någonsin kördes.

    Kastar vid fel. Ett saknat ivfflat-index syns aldrig i ett svar (bara
    som sekventiell scan i last), så en tyst varning är en tyst degradering.

    Returnerar dict:
        {'table', 'converted', 'index_created', 'skipped'}
    där `skipped` är en orsakskod när steget inte kunde köras alls
    ('table_missing', 'column_missing', 'no_vector_extension').
    """
    # Registrera psycopg2-adaptern först: utan den läses kolumnen som en
    # rå sträng och hamnar oconverterad i Odoos cache (api.py insert_missing
    # går inte via convert_to_cache). Adaptern gör att läsning ger en lista.
    from .fields.pg_vector import register_vector_adapter
    register_vector_adapter(cr)

    result = {
        'table': table,
        'converted': False,
        'index_created': False,
        'skipped': None,
    }

    # Namn kan inte parametriseras i DDL — validera dem.
    for ident in (table, column):
        if not _IDENT_RE.match(ident or ''):
            raise ValueError(
                'ensure_vector_column: ogiltigt identifierarnamn %r' % (ident,))

    # (a) Tabellen måste finnas. Anroparen kan köra före tabellskapande —
    # det är ett förväntat tillstånd, inte ett fel.
    cr.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = %s AND table_schema = 'public'
    """, (table,))
    if not cr.fetchone():
        result['skipped'] = 'table_missing'
        return result

    # (b) Utan vector-extensionen kan ingen vektor lagras alls. Det är ett
    # annat och större fel än ett trasigt index — hjälparen ska inte fälla
    # uppgraderingen för det, men det ska synas.
    cr.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    if not cr.fetchone():
        _logger.warning(
            'The pgvector extension is missing — skipping %s.%s. Run: salt \'*\' state.apply postgres.pgvector', table, column)
        result['skipped'] = 'no_vector_extension'
        return result

    # (c) Kolumnen måste finnas.
    before = _vector_column_type(cr, table, column)
    if before is None:
        result['skipped'] = 'column_missing'
        return result

    # (d) Konvertera. Läs om typen efteråt — aldrig besluta på `before`.
    if before == 'text':
        # Textvärden skrivna som '[0.1,0.2,...]' är giltiga vector-literaler.
        # NULL bevaras som NULL i stället för att fälla hela ALTER:en.
        cr.execute(
            'ALTER TABLE {t} ALTER COLUMN {c} TYPE vector({d}) '
            'USING CASE WHEN {c} IS NULL THEN NULL ELSE {c}::vector({d}) END'
            .format(t=table, c=column, d=int(dim)))
        result['converted'] = True
        _logger.info('%s.%s: text → vector(%s)', table, column, dim)
    elif before == 'vector':
        # Dimensionslös vector kan inte castas till en dimension — och den
        # är oanvändbar för ivfflat. Värdena nollställs medvetet.
        cr.execute(
            'ALTER TABLE {t} ALTER COLUMN {c} TYPE vector({d}) USING NULL'
            .format(t=table, c=column, d=int(dim)))
        result['converted'] = True
        _logger.info(
            '%s.%s: dimensionless vector -> vector(%s) (dimensionless vectors cannot be cast; zeroed)',
            table, column, dim)

    after = _vector_column_type(cr, table, column)
    if after != 'vector(%s)' % dim:
        raise Exception(
            'ensure_vector_column: %s.%s is %r after conversion, expected vector(%s)' % (table, column, after, dim))

    # (e) Indexet — i SAMMA anrop som konverteringen.
    index_name = 'idx_%s_embedding' % table
    cr.execute(
        'SELECT 1 FROM pg_indexes WHERE tablename = %s AND indexname = %s',
        (table, index_name))
    if not cr.fetchone():
        cr.execute(
            'CREATE INDEX {i} ON {t} USING ivfflat ({c} vector_cosine_ops) '
            'WITH (lists = 100)'.format(i=index_name, t=table, c=column))
        result['index_created'] = True
        _logger.info('Created ivfflat index %s on %s.%s',
                     index_name, table, column)

    return result


def post_init_hook_personal_memory(env):
    """Post-install hook for ai.personal.memory (Odoo 18 — takes env).
    
    Skapar pgvector-kolumn, tsvector GENERATED COLUMN och index.
    Idempotent — körs endast om tabellen finns och kolumner saknas.
    """
    cr = env.cr

    # Verify required PostgreSQL extensions
    try:
        cr.execute("SELECT extname FROM pg_extension WHERE extname IN ('age', 'vector')")
        installed = {r[0] for r in cr.fetchall()}
        missing = {'age', 'vector'} - installed
        if missing:
            _logger.warning(
                'Missing PostgreSQL extensions: %s. '
                'Run: salt \'*\' state.apply postgres.age',
                ', '.join(missing))
        if 'age' not in installed:
            _logger.warning('AGE extension missing — Odoo Mind graph disabled')
        if 'vector' not in installed:
            _logger.warning('vector extension missing — pgvector embeddings disabled')
    except Exception as e:
        _logger.warning('Extension check failed (non-fatal): %s', e)

    # ════════════════════════════════════════════
    # AI Personal Memory SQL (non-fatal — table may not exist yet)
    # ════════════════════════════════════════════
    cr.execute('SAVEPOINT pre_personal_memory_sql')
    try:
        cr.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'ai_personal_memory' AND table_schema = 'public'")
        if cr.fetchone():
            # tsvector GENERATED COLUMN
            cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'ai_personal_memory' AND column_name = 'search_vector'")
            if not cr.fetchone():
                cr.execute("ALTER TABLE ai_personal_memory ADD COLUMN search_vector tsvector GENERATED ALWAYS AS (to_tsvector('swedish', coalesce(content, ''))) STORED")
                _logger.info('Created search_vector column on ai_personal_memory')

            # GIN-index
            cr.execute("SELECT 1 FROM pg_indexes WHERE tablename = 'ai_personal_memory' AND indexname = 'idx_ai_personal_memory_fts'")
            if not cr.fetchone():
                cr.execute("CREATE INDEX idx_ai_personal_memory_fts ON ai_personal_memory USING GIN(search_vector)")
                _logger.info('Created GIN index on search_vector')

            # B-tree-index
            cr.execute("SELECT 1 FROM pg_indexes WHERE tablename = 'ai_personal_memory' AND indexname = 'idx_ai_personal_memory_user_id'")
            if not cr.fetchone():
                cr.execute("CREATE INDEX idx_ai_personal_memory_user_id ON ai_personal_memory (user_id, create_date DESC)")
                _logger.info('Created B-tree index on user_id')
    except Exception as e:
        _logger.warning('SQL migration for ai.personal.memory failed (non-fatal): %s', e)
        try:
            cr.execute('ROLLBACK TO SAVEPOINT pre_personal_memory_sql')
        except Exception:
            cr.rollback()

    # ── Vektorsteget: PROPAGERAR fel (till skillnad från B-tree/GIN) ──
    #
    # Ett saknat ivfflat-index gör sökningen långsam men korrekt — det syns
    # aldrig i ett svar, bara i last. En varning i uppgraderingsloggen är
    # därför en tyst degradering. Det här steget får fälla uppgraderingen.
    ensure_vector_column(cr, 'ai_personal_memory')

    # ════════════════════════════════════════════
    # Company Memory SQL
    # ════════════════════════════════════════════
    cr.execute('SAVEPOINT pre_company_memory_sql')
    try:
        cr.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'ai_company_memory' AND table_schema = 'public'")
        if cr.fetchone():
            cr.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'ai_company_memory' AND column_name = 'search_vector'")
            if not cr.fetchone():
                cr.execute("ALTER TABLE ai_company_memory ADD COLUMN search_vector tsvector GENERATED ALWAYS AS (to_tsvector('swedish', coalesce(content, ''))) STORED")
                _logger.info('Created search_vector on ai_company_memory')

            cr.execute("SELECT 1 FROM pg_indexes WHERE tablename = 'ai_company_memory' AND indexname = 'idx_ai_company_memory_fts'")
            if not cr.fetchone():
                cr.execute("CREATE INDEX idx_ai_company_memory_fts ON ai_company_memory USING GIN(search_vector)")

            cr.execute("SELECT 1 FROM pg_indexes WHERE tablename = 'ai_company_memory' AND indexname = 'idx_ai_company_memory_company'")
            if not cr.fetchone():
                cr.execute("CREATE INDEX idx_ai_company_memory_company ON ai_company_memory (company_id, create_date DESC)")
    except Exception as e:
        _logger.warning('SQL migration for ai.company.memory failed (non-fatal): %s', e)
        try:
            cr.execute('ROLLBACK TO SAVEPOINT pre_company_memory_sql')
        except Exception:
            cr.rollback()

    # ── Vektorsteget: PROPAGERAR fel (samma skäl som för personal) ──
    ensure_vector_column(cr, 'ai_company_memory')

    # ════════════════════════════════════════════
    # AGE Graph initialization (Odoo Mind)
    # ════════════════════════════════════════════
    cr.execute('SAVEPOINT pre_age_init')
    try:
        cr.execute("SELECT 1 FROM pg_extension WHERE extname = 'age'")
        if cr.fetchone():
            cr.execute("SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'odoo_mind'")
            if not cr.fetchone():
                cr.execute("SELECT * FROM ag_catalog.create_graph('odoo_mind')")
                _logger.info('Created odoo_mind graph')
            else:
                _logger.info('odoo_mind graph already exists')
        else:
            _logger.info('AGE extension not installed — skipping graph init')
    except Exception as e:
        _logger.warning('Odoo Mind graph initialization failed (non-fatal): %s', e)
        try:
            cr.execute('ROLLBACK TO SAVEPOINT pre_age_init')
        except Exception:
            cr.rollback()

    # ════════════════════════════════════════════
    # Create company memory crons
    # ════════════════════════════════════════════
    _CRONS = [
        ('Company Memory Nightly Consolidation', 'model.cron_nightly_consolidation()', 2, 0, 5),
        ('Company Memory Partner Customers', 'model.cron_index_partners()', 3, 0, 5),
        ('Company Memory Partner Suppliers', 'model.cron_index_suppliers()', 3, 30, 5),
        ('Company Memory Knowledge Articles', 'model.cron_index_knowledge()', 4, 0, 5),
        ('Company Memory DMS Documents', 'model.cron_index_dms()', 4, 30, 5),
        ('Company Memory Website RAG', 'model.cron_index_website()', 5, 0, 5),
        ('Company Memory Strategy', 'model.cron_index_strategy()', 5, 30, 5),
        ('Company Memory Management Summary', 'model.cron_generate_management_summary()', 6, 0, 5),
    ]
    for name, code, hour, minute, priority in _CRONS:
        try:
            cron = env['ir.cron'].search([
                ('cron_name', '=', name),
                ('model_id.model', '=', 'ai.company.memory'),
            ], limit=1)
            if not cron:
                model_id = env['ir.model']._get('ai.company.memory')
                env['ir.cron'].create({
                    'cron_name': name,
                    'model_id': model_id.id,
                    'state': 'code',
                    'code': code,
                    'interval_number': 1,
                    'interval_type': 'days',
                    'numbercall': -1,
                    'active': True,
                    'priority': priority,
                    'user_id': env.ref('base.user_root').id,
                    'hour': hour,
                    'minute': minute,
                })
                _logger.info('Created cron: %s', name)
        except Exception as e:
            _logger.warning('Could not create cron %s: %s', name, e)

    # ── Org init (default coworker + templates) ──
    try:
        post_init_hook_org(env)
        env.flush_all()
        env.cr.commit()
    except Exception as e:
        _logger.warning('Org init failed (non-fatal): %s', e)


GRILL_BLOCK = """## Interview protocol (GRILL)

Grill the user about the decisions — within a budget.

RULES:
1. ONE question per message. Never batch questions.
2. FACTS you look up yourself (your research/inventory tools).
   Only DECISIONS go to the user.
3. Every question includes YOUR RECOMMENDED answer, and why.
4. BUDGET: max 5 questions. Show the counter: "Question 2/5".
5. AUTOPILOT: if the user says "kör", "auto" or "you decide",
   stop asking — decide the rest yourself.
6. When the budget is spent (or autopilot is on): make the
   remaining decisions yourself and mark them as yours.
7. CONFIRM before building: summarize the shared understanding —
   the user's decisions AND your defaults (with rationale).
   Wait for explicit OK. This summary is a review, not a
   question — it does not count against the budget.
"""

BUILDER_SYSTEM_PROMPT = """You are a Quest Architect — an AI specialized in designing and building
AI quests for the Odoo AI platform.

## Your Process

1. INVENTORY — when the user wants to build or configure a quest, start
   with inventory_architecture() to get the complete system model: all ai.*
   models with fields/relations, init_types, and MODULE.md docs from
   installed modules. Then call inventory_skills(), inventory_agents(),
   inventory_models(), inventory_tools(), inventory_identities(),
   inventory_quests(). (For greetings or vague messages there is nothing
   to inventory — go straight to GRILL.)

2. GRILL — interview the user about the DECISIONS for this quest, following
   the Interview protocol below. Typical decisions: what the ONE thing this
   quest must do well is, how users will trigger it (chat, powerbox, email,
   cron), what a successful response looks like, what models or data it needs.

3. DESIGN — after the protocol's CONFIRM step, propose a complete quest
   architecture:
   - Quest name and description (system prompt)
   - Which init_types and WHY they fit the use case
   - Supervisor mode? Why multi-agent vs single-agent?
   - Each agent: name, model (match to task: reasoning→Claude,
     speed/drafting→cerebras, vision→vision models), skills
   - New skills needed: write complete recipe texts
   - Which Odoo models to bind (for powerbox)
   - Explain the WHY behind choices — not just WHAT

4. ITERATE — present the plan clearly. Ask "What would you change?"

5. EXECUTE — only when user says "execute" or "kör":
   - Create skills first, then agents, then quest, then assignments
   - Configure init types
   - Report exactly what was created with IDs

6. TEST (NEW) — after execution, offer:
   "Want me to generate 3 test prompts to verify the quest works?"
   Generate realistic prompts, run them, show results.

{GRILL}

## Rules
- When building or configuring, start with inventory_architecture() — it is your source of truth
- When designing new skills, read similar ones with read_skill() for patterns
- Explain the WHY — theory of mind, not rigid MUSTs
- Keep system prompts lean — remove what does not pull its weight
- Match model capabilities to agent tasks
- Never execute without explicit approval
- Every agent should have exactly the skills it needs, no more
""".replace('{GRILL}', GRILL_BLOCK)

SKILL_BUILDER_PROMPT = """You are a Skill Architect — an AI specialized in designing
reusable AI skills for Odoo's ai.skill system.

A skill is a markdown recipe that tells an AI agent how to
perform a specific task. Good skills are:
- Comprehensive enough to handle edge cases
- General enough to work across varied prompts
- Structured with clear output formats and examples
- Explanatory — explain WHY, not just WHAT

## Your Process

1. RESEARCH — when the user wants to build or improve a skill, arm
   yourself with facts BEFORE asking anything. (For greetings or vague
   messages there is nothing to research — go straight to GRILL.):
   a. Call inventory_skills() — check existing Odoo skills
   b. Call read_skill(id) — read full recipe of similar skills
   c. Use search_github_skills() to find community skills on GitHub
   d. Use fetch_url() to read promising SKILL.md files

2. GRILL — interview the user about the DECISIONS for this skill,
   following the Interview protocol below. Typical decisions: scope
   boundaries, branches (distinct use cases), output format, trigger
   words, edge cases.

3. DRAFT — after the protocol's CONFIRM step, write the complete
   recipe_text:
   a. Overview: what the skill does and why
   b. Process: step-by-step instructions (imperative form)
   c. Output format: exact template with examples
   d. Edge cases: how to handle common pitfalls
   e. Dependencies: tools/models needed
   
   Write in markdown. Use imperative form.
   Include Input → Output examples.
   Explain the WHY — "Do X because it prevents Y" not "ALWAYS do X".

4. EVALUATE — when the user approves the draft:
   a. Generate 3 realistic test prompts
   b. Run each through builder_test_skill()
   c. Show results to the user
   d. Ask: "Does this look right? What should I improve?"

5. ITERATE — based on user feedback:
   a. Identify patterns in what went wrong
   b. Generalize fixes (don't overfit to test cases)
   c. Keep the recipe lean — remove what's not working
   d. Repeat evaluate → iterate until user is satisfied

6. OPTIMIZE — final touches:
   a. Tune trigger_keywords for reliable triggering
   b. Set appropriate category
   c. Verify description is within 1024 chars
   d. Call builder_create_skill() to save

{GRILL}

## Skill Recipe Template
```markdown
# [Skill Name]

## Overview
[One paragraph explaining what this skill does and why]

## When to Use
- [Trigger context]

## Process
### Step 1: [Name]
[Instructions — imperative form, explain WHY]

## Output Format
[Template with placeholders]

**Example:**
Input: "user prompt"
Output: "expected output"

## Edge Cases
- [Case]: [How to handle]

## Dependencies
- Tools: [tool1, tool2]
```

## Rules
- When building or improving, start with inventory_skills() — avoid duplicates
- Read similar skills with read_skill() to learn patterns
- Explain the WHY — theory of mind, not rigid MUSTs
- Generate test prompts that are realistic and varied
- Don't execute without user approval
- Keep recipes lean — remove what doesn't pull its weight
""".replace('{GRILL}', GRILL_BLOCK)

def post_init_hook_org(env):
    """Load templates for the organization layer.

    Default-coworkern "Allmän assistent" definieras numera som data-XML
    (data/default_coworker.xml) med xmlids; befintliga installationer
    adopteras av migrations/1.21/pre-migrate.py.
    """
    import os, json

    # 1. Load templates from JSON files
    try:
        template_model = env['ai.org.template']
        template_model.load_all_templates()
        _logger.info('Templates loaded')
    except Exception as e:
        _logger.warning('Template loading skipped (non-fatal): %s', e)

    _logger.info('post_init_hook_org complete')


def okf_ensure_search_infrastructure(env):
    """Skapa `search_vector` + index på `ai_okf_concept` (okf-recall-path fas 11).

    VARFÖR DENNA FUNKTION FINNS:
    Migration 1.11 gjorde exakt detta, men körde **före** ORM:en skapade
    tabellen `ai_okf_concept`. Varje `ALTER TABLE` slog i en icke-existerande
    tabell och svaldes av `except: _logger.warning('non-fatal')`. Loggen sa
    "Created search_vector on ai_okf_concept" medan kolumnen aldrig uppstod.
    Bevis i drift: varken `search_vector`, GIN-indexet, ivfflat-indexet eller
    B-tree-indexet finns i `social` — endast ORM:ens pkey och unique-index.

    Denna funktion körs i `post_init_hook`, dvs **efter** att tabellen finns,
    och propagerar fel istället för att svälja dem.

    Idempotent: varje steg kontrollerar information_schema/pg_indexes först.

    Raises:
        Exception: om ett steg misslyckas. Uppgraderingen SKA rapportera fel —
            en tyst tom sökväg är värre än ett högljutt fel.
    """
    cr = env.cr

    # Kontrollera att tabellen finns — annars är anropet felplacerat
    cr.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'ai_okf_concept' AND table_schema = 'public'
    """)
    if not cr.fetchone():
        raise Exception(
            'The ai_okf_concept table does not exist — okf_ensure_search_infrastructure must run after table creation'
        )

    # 1. search_vector — GENERATED STORED över summary + title + source_text
    #
    # `source_text` lades till 2026-11-05: fältet bär postens FAKTISKA
    # innehåll (mailens brödtext, dokumentets text), medan `summary` är en
    # deterministisk etikett på 1–2 rader. Utan source_text i vektorn var
    # brödtexten osökbar trots att den lagrades — mätt på luke18: konceptet
    # för ett mail innehöll ordet "förfallodatum" i `source_text`, men
    # `_okf_search('förfallodatum')` gav noll träffar. Det gjorde hela
    # mail-indexeringen meningslös för sitt syfte: "hitta mailet där det
    # står".
    #
    # Kolumnen är GENERATED, så den kan inte ändras med ALTER utan att
    # släppas och byggas om. Vi kontrollerar uttrycket och bygger om när
    # source_text saknas i det — idempotent, och en befintlig vektor utan
    # source_text uppgraderas vid första körningen.
    cr.execute("""
        SELECT column_name, generation_expression
          FROM information_schema.columns
         WHERE table_name = 'ai_okf_concept' AND column_name = 'search_vector'
    """)
    row = cr.fetchone()
    has_source = bool(row and row[1] and 'source_text' in row[1])
    if row and not has_source:
        cr.execute('ALTER TABLE ai_okf_concept DROP COLUMN search_vector')
        _logger.info('OKF: bygger om search_vector (source_text saknades)')
        row = None
    if not row:
        cr.execute("""
            ALTER TABLE ai_okf_concept
            ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                to_tsvector('swedish',
                            coalesce(summary, '') || ' ' || coalesce(title, '')
                            || ' ' || coalesce(source_text, ''))
            ) STORED
        """)
        _logger.info('OKF: created search_vector on ai_okf_concept (Swedish FTS over summary + title + source_text)')

    # 2. GIN-index för fulltext
    cr.execute("""
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'ai_okf_concept'
          AND indexname = 'idx_ai_okf_concept_fts'
    """)
    if not cr.fetchone():
        cr.execute("""
            CREATE INDEX idx_ai_okf_concept_fts
            ON ai_okf_concept USING GIN(search_vector)
        """)
        _logger.info('OKF: skapade GIN-index idx_ai_okf_concept_fts')

    # 3. ivfflat-index över embedding (kräver pgvector + rätt kolumntyp)
    cr.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    if cr.fetchone():
        cr.execute("""
            SELECT data_type, udt_name FROM information_schema.columns
            WHERE table_name = 'ai_okf_concept' AND column_name = 'embedding'
        """)
        row = cr.fetchone()
        if row and row[0] == 'USER-DEFINED':
            # Migration 1.11 steg 4 (ALTER COLUMN TYPE vector(1024)) misslyckades
            # OCKSÅ tyst: kolumnen blev dimensionslös `vector`, vilket gör
            # ivfflat omöjligt ("column does not have dimensions").
            cr.execute("""
                SELECT format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                JOIN pg_class c ON a.attrelid = c.oid
                WHERE c.relname = 'ai_okf_concept' AND a.attname = 'embedding'
            """)
            fmt = cr.fetchone()
            if fmt and fmt[0] == 'vector':
                cr.execute("""
                    ALTER TABLE ai_okf_concept
                    ALTER COLUMN embedding TYPE vector(1024)
                    USING NULL
                """)
                _logger.info(
                    'OKF: set embedding to vector(1024) — the column was dimensionless since migration 1.11 failed silently')

            cr.execute("""
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'ai_okf_concept'
                  AND indexname = 'idx_ai_okf_concept_embedding'
            """)
            if not cr.fetchone():
                cr.execute("""
                    CREATE INDEX idx_ai_okf_concept_embedding
                    ON ai_okf_concept
                    USING ivfflat (embedding vector_cosine_ops)
                    WITH (lists = 100)
                """)
                _logger.info('OKF: created ivfflat index over embedding')

    # 4. B-tree för versionsuppslag (scope, concept_key, version DESC)
    cr.execute("""
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'ai_okf_concept'
          AND indexname = 'idx_ai_okf_concept_scope_key'
    """)
    if not cr.fetchone():
        cr.execute("""
            CREATE INDEX idx_ai_okf_concept_scope_key
            ON ai_okf_concept (scope, concept_key)
        """)
        _logger.info('OKF: skapade B-tree-index (scope, concept_key)')


def memory_ensure_search_infrastructure(env):
    """Skapa `search_vector` + index på `ai_memory` och `ai_memory_chunk`.

    Samma mönster som `okf_ensure_search_infrastructure`, av samma skäl:
    kolumnerna måste skapas EFTER att ORM:en byggt tabellen. En migration
    som kör `ALTER TABLE` före tabellen finns slår i tomma luften, och om
    felet sväljs (`except: _logger.warning('non-fatal')`) står det "klart"
    i loggen medan kolumnen aldrig uppstod — exakt vad som hände i
    migration 1.11.

    Idempotent: varje steg kontrollerar information_schema/pg_indexes först.

    Raises:
        Exception: om ett steg misslyckas. En tyst tom sökväg är värre än
            ett högljutt fel.
    """
    cr = env.cr

    # ── ai_memory ────────────────────────────────────────────────────
    cr.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'ai_memory' AND table_schema = 'public'
    """)
    if not cr.fetchone():
        raise Exception(
            'The ai_memory table does not exist — memory_ensure_search_infrastructure must run after table creation (post_init_hook, not a migration)'
        )

    # 1. search_vector — GENERATED STORED över okf_body + name (svensk stemming)
    #    `okf_body` kommer från ai.okf.format.mixin (ai-memory-okf-format).
    #    Fram till dess att den mixinen finns bär `content` texten, så
    #    kolumnen faller tillbaka på den om okf_body saknas.
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'ai_memory' AND column_name = 'search_vector'
    """)
    if not cr.fetchone():
        cr.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'ai_memory' AND column_name = 'okf_body'
        """)
        body_col = 'okf_body' if cr.fetchone() else 'content'
        cr.execute("""
            ALTER TABLE ai_memory
            ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                to_tsvector('swedish',
                            coalesce(%s, '') || ' ' || coalesce(name, ''))
            ) STORED
        """ % body_col)
        _logger.info(
            'Memory: created search_vector on ai_memory (Swedish FTS over %s + name)', body_col)

    # 2. GIN-index för fulltext
    cr.execute("""
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'ai_memory' AND indexname = 'idx_ai_memory_fts'
    """)
    if not cr.fetchone():
        cr.execute("""
            CREATE INDEX idx_ai_memory_fts
            ON ai_memory USING GIN(search_vector)
        """)
        _logger.info('Memory: skapade GIN-index idx_ai_memory_fts')

    # 3. ivfflat-index över embedding (kräver pgvector + rätt kolumntyp)
    cr.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    if cr.fetchone():
        cr.execute("""
            SELECT format_type(a.atttypid, a.atttypmod)
            FROM pg_attribute a
            JOIN pg_class c ON a.attrelid = c.oid
            WHERE c.relname = 'ai_memory' AND a.attname = 'embedding'
        """)
        fmt = cr.fetchone()
        if fmt and fmt[0] == 'vector':
            # Dimensionslös `vector` gör ivfflat omöjligt
            # ("column does not have dimensions").
            cr.execute("""
                ALTER TABLE ai_memory
                ALTER COLUMN embedding TYPE vector(1024)
                USING NULL
            """)
            _logger.info(
                'Memory: set embedding to vector(1024) — the column was dimensionless')

        cr.execute("""
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'ai_memory'
              AND indexname = 'idx_ai_memory_embedding'
        """)
        if not cr.fetchone():
            cr.execute("""
                CREATE INDEX idx_ai_memory_embedding
                ON ai_memory USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100)
            """)
            _logger.info('Memory: created ivfflat index over embedding')

    # ── ai_memory_chunk ──────────────────────────────────────────────
    # Chunkarna bär sin egen text och vektor; BM25 sker på chunkens
    # content (förälderns search_vector räcker inte för en 900-sidig PDF).
    cr.execute("""
        SELECT 1 FROM information_schema.tables
        WHERE table_name = 'ai_memory_chunk' AND table_schema = 'public'
    """)
    if not cr.fetchone():
        # Modellen är inte installerad än (T2 i ai-memory-pgvector) —
        # post_init kan köra före den finns. Inte ett fel: nästa
        # uppgradering skapar kolumnerna.
        _logger.info(
            'Memory: ai_memory_chunk does not exist yet — skipping the chunk index (created on the next upgrade)')
        return

    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'ai_memory_chunk' AND column_name = 'search_vector'
    """)
    if not cr.fetchone():
        cr.execute("""
            ALTER TABLE ai_memory_chunk
            ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                to_tsvector('swedish', coalesce(content, ''))
            ) STORED
        """)
        _logger.info('Memory: created search_vector on ai_memory_chunk')

    cr.execute("""
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'ai_memory_chunk'
          AND indexname = 'idx_ai_memory_chunk_fts'
    """)
    if not cr.fetchone():
        cr.execute("""
            CREATE INDEX idx_ai_memory_chunk_fts
            ON ai_memory_chunk USING GIN(search_vector)
        """)
        _logger.info('Memory: skapade GIN-index idx_ai_memory_chunk_fts')

    cr.execute("""
        SELECT format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a
        JOIN pg_class c ON a.attrelid = c.oid
        WHERE c.relname = 'ai_memory_chunk' AND a.attname = 'embedding'
    """)
    fmt = cr.fetchone()
    if fmt and fmt[0] == 'vector':
        cr.execute("""
            ALTER TABLE ai_memory_chunk
            ALTER COLUMN embedding TYPE vector(1024)
            USING NULL
        """)
        _logger.info(
            'Memory: satte ai_memory_chunk.embedding till vector(1024)')

    cr.execute("""
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'ai_memory_chunk'
          AND indexname = 'idx_ai_memory_chunk_embedding'
    """)
    if not cr.fetchone():
        cr.execute("""
            CREATE INDEX idx_ai_memory_chunk_embedding
            ON ai_memory_chunk USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100)
        """)
        _logger.info(
            'Memory: created ivfflat index over ai_memory_chunk.embedding')


def post_init_hook(env):
    """Create Quest Builder and Skill Builder quests if they don't exist."""

    # Run org init too
    post_init_hook_org(env)

    # Default-modellen (agent-model-resolution D3).
    #
    # `get_default_provider()` läser `ai_agent_core.default_model_id`, men
    # parametern sattes aldrig — och 20 av 21 agenter saknade `model_id`.
    # Utan en default kunde ingen av dem köra.
    _ensure_default_model(env)

    # OKF-sökvägen: search_vector + index (okf-recall-path fas 11).
    # Körs här och inte i migration 1.11 — där fanns inte tabellen ännu.
    okf_ensure_search_infrastructure(env)

    # Minnessökvägen: search_vector + index på ai_memory (ai-memory-pgvector).
    # Samma skäl som ovan — kolumnerna kräver att tabellen finns.
    memory_ensure_search_infrastructure(env)

    # Personal/company memory: search_vector + GIN + pgvector-index.
    #
    # VARFÖR DEN ANROPAS HÄR: manifestet pekade tidigare på
    # post_init_hook_personal_memory som 'post_init_hook', vilket gjorde att
    # DENNA funktion (post_init_hook) aldrig kördes — och därmed varken
    # _ensure_default_model, okf_ensure_search_infrastructure eller
    # Quest/Skill Builder. Följden i drift: kolumnen search_vector saknades
    # på ai_personal_memory, BM25-sökningen kraschade med
    # 'column "search_vector" does not exist', transaktionen förgiftades
    # (InFailedSqlTransaction) och /ai/stream svarade HTTP 500 →
    # "Anslutningen till AI-servern bröts" (session 21772, 2026-09-22).
    post_init_hook_personal_memory(env)

    # Kanal-registreringen (channel-adapter-wiring 4.5).
    #
    # `channel_registry` är en process-global singleton. Att registrera vid
    # import hade räckt för en worker som importerar modulen, men hooken är
    # den plats där modulen redan säkrar annat som ska finnas efter en
    # (upp)installation — och registreringen är idempotent.
    try:
        from .core.channels import register_mail_channel
        register_mail_channel()
        _logger.info('channel_registry: mail-kanalen registrerad')
    except Exception as e:
        _logger.warning(
            'channel_registry: kunde inte registrera mail-kanalen: %s', e)

    # Quest Builder
    if not env['ai.coworker'].search_count([('name', '=', 'Quest Builder')]):
        env['ai.coworker'].create({
            'name': 'Quest Builder',
            'description': BUILDER_SYSTEM_PROMPT,
            'sub_description': 'AI that helps you build and configure quests',
            'init_type': 'manual',
            'status': 'active',
            'show_in_chat': False,
            'use_chat_history': True,
            'use_time_context': True,
        })
        _logger.info('Created Quest Builder quest')
    else:
        _logger.info('Quest Builder already exists — skipping')

    # Skill Builder
    if not env['ai.coworker'].search_count([('name', '=', 'Skill Builder')]):
        env['ai.coworker'].create({
            'name': 'Skill Builder',
            'description': SKILL_BUILDER_PROMPT,
            'sub_description': 'AI that helps you design and test skills',
            'init_type': 'manual',
            'status': 'active',
            'show_in_chat': False,
            'use_chat_history': True,
            'use_time_context': True,
        })
        _logger.info('Created Skill Builder quest')
    else:
        _logger.info('Skill Builder already exists — skipping')

    # ════════════════════════════════════════════
    # AGE Graph initialization (Odoo Mind)
    # ════════════════════════════════════════════
    try:
        cr = env.cr
        # 1. AGE extension managed by SaltStack/DBA
        # AGE extension handled by SaltStack/DBA — skipping
        _logger.info('AGE extension skipped — managed by DBA')

        # 2. Create graph if not exists
        #
        # SAVEPOINT: `ag_catalog` ägs av postgres-superusern, så app-rollen
        # får `permission denied` på `ag_graph` även när AGE är installerat.
        # Utan savepoint förgiftar det transaktionen (InFailedSqlTransaction)
        # och ALLT efteråt i hooken dör — inklusive ir_attachment.create för
        # Quest/Skill Builders hr.employee-bilder (FYND 2026-09-23, ren
        # nyinstallation i okf_mixin_test: exit 255).
        #
        # Samma mönster som is_age_available() i ai_graph_node.py: ett fel här
        # är inte fatalt, det betyder bara att grafen inte är användbar.
        cr.execute('SAVEPOINT age_graph_init')
        try:
            cr.execute("SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'odoo_mind'")
            if not cr.fetchone():
                cr.execute("SELECT * FROM ag_catalog.create_graph('odoo_mind')")
                _logger.info('Created odoo_mind graph')
            else:
                _logger.info('odoo_mind graph already exists')
            cr.execute('RELEASE SAVEPOINT age_graph_init')
        except Exception as e:
            cr.execute('ROLLBACK TO SAVEPOINT age_graph_init')
            _logger.warning(
                'The AGE graph could not be initialised (%s) — the graph is off, the rest of the hook continues. Run GRANT on ag_catalog for the app role if the graph is to be used.', e)

        # 3. Create cron_sync_graph if not exists (delad med
        # _ensure_graph_cron så uppgraderade system också får den).
        _ensure_graph_cron(env)

        # 4. Bulk index base nodes: res.partner → :OdooPartner
        partner_def = env['graph.node.definition'].search([
            ('graph_label', '=', 'OdooPartner'),
        ], limit=1)
        if partner_def:
            partner_def._sync_batch(env['res.partner'])
            _logger.info('Bulk indexed res.partner into AGE graph')

        # 4. Bulk index base nodes: res.company → :Company
        company_def = env['graph.node.definition'].search([
            ('graph_label', '=', 'Company'),
        ], limit=1)
        if company_def:
            company_def._sync_batch(env['res.company'])
            _logger.info('Bulk indexed res.company into AGE graph')

        # 5. Set version marker
        env['ir.config_parameter'].sudo().set_param(
            'odoomind.version', '1')
        _logger.info('Odoo Mind graph initialized')

    except Exception as e:
        _logger.warning(
            'Odoo Mind graph initialization failed (non-fatal): %s', e)
        _logger.warning(
            'Apache AGE may not be installed. '
            'Run: salt \'*\' state.apply postgres.age')


def _ensure_graph_cron(env):
    """Säkerställ att graf-synken finns som `cron.task` (idempotent).

    `post_init_hook` skapade förr en `ir.cron` här. Ett system som
    installerade `ai_agent_core` innan grafen fanns fick den aldrig — och
    grafen förblev tyst tom (0 noder, `last_sync = NULL` på alla
    definitioner, ingen cron). Samma mönster som `_ensure_webhook_config()`
    i saltstack-modulen: skapa vid behov, inte bara vid install.

    **Varför cron.task och inte ir.cron** (2026-10-25): en `ir.cron` som
    skrivs vid modulladdning kan inte uppdateras medan jobbet kör
    ("Record cannot be modified right now"), och en post som skapas här
    fick ingen xmlid — den gick varken att spåra eller uppgradera. Jobbet
    ligger nu i `data/cron_graph_sync.xml`; den här funktionen är bara en
    efterfyllnad för system som installerade modulen innan datafilen
    fanns.

    Anropas från post_init_hook och från settings-öppning.
    """
    Task = env['cron.task'].sudo()
    task = Task.search([('technical_name', '=', 'ai_graph_sync')], limit=1)
    if task:
        return task
    if 'graph.node.definition' not in env:
        return False
    task = Task.create({
        'name': 'Odoo Mind Graph Sync',
        'technical_name': 'ai_graph_sync',
        'module': 'ai_agent_core',
        'model_name': 'graph.node.definition',
        'method_name': '_sync_all',
        'interval_number': 5,
        'interval_type': 'minutes',
        'active': True,
    })
    _logger.info('Created cron.task: Odoo Mind Graph Sync')
    return task


def _ensure_default_model(env):
    """Sätt `ai_agent_core.default_model_id` om den saknas.

    Väljer den billigaste aktiva modellen — `cheap` om den finns, annars
    första bästa. Att välja en dyr modell som default vore att fatta ett
    kostnadsbeslut i smyg (agent-model-resolution D3).
    """
    param = 'ai_agent_core.default_model_id'
    existing = env['ir.config_parameter'].sudo().get_param(param)
    if existing:
        model = env['ai.model'].sudo().browse(int(existing))
        if model.exists():
            _logger.info('Default-modell finns redan: %s', model.name)
            return

    Model = env['ai.model'].sudo()
    model = Model.search([('name', '=', 'cheap'), ('active', '=', True)],
                         limit=1)
    if not model:
        model = Model.search([('active', '=', True)], limit=1)
    if not model:
        _logger.warning(
            'No active ai.model exists — the default model could not be set. Agents without model_id cannot run.')
        return

    env['ir.config_parameter'].sudo().set_param(param, str(model.id))
    _logger.info('Default-modell satt till %s (id=%d)', model.name, model.id)

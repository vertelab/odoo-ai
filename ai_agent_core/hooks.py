# -*- coding: utf-8 -*-
"""Post-install hook: create Quest Builder and Skill Builder quests."""

import logging

_logger = logging.getLogger(__name__)

GRAPH_NAME = 'odoo_mind'


def _age_graph_exists(cr):
    """Return True if the AGE graph is usable by the current role.

    OBS: vi testar INTE med `SELECT 1 FROM ag_catalog.ag_graph`. Det
    schemat ägs av postgres-superusern, så en vanlig Odoo-roll får
    `permission denied` där — även när AGE är installerat och grafen
    fungerar. Testet gav då falskt negativt och loggade "graph init
    failed" på ett friskt system.

    cypher() är den väg anroparen faktiskt använder; fungerar den är
    grafen användbar. Anroparen ansvarar för SAVEPOINT runt anropet.
    """
    cr.execute("SELECT 1 FROM pg_extension WHERE extname = 'age'")
    if not cr.fetchone():
        return False
    cr.execute(
        "SELECT * FROM ag_catalog.cypher(%s, $$ RETURN 1 $$) "
        "AS (x ag_catalog.agtype)",
        (GRAPH_NAME,))
    return cr.fetchone() is not None


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
            'ai_agent_core installation blocked: ai_agent är installerad '
            '(state=%s). Modulerna är inkompatibla — avinstallera ai_agent '
            'innan du installerar ai_agent_core.', row[0])
        from odoo.exceptions import UserError
        raise UserError(
            'ai_agent_core kan inte installeras samtidigt som ai_agent.\n\n'
            'De definierar samma modeller/vyer (ai.tool, view_ai_tool_*,\n'
            'action_ai_tool) och är inkompatibla. Avinstallera ai_agent '
            'först (Appar → ai_agent → Avinstallera).'
        )


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

            # pgvector: konvertera text → vector(1024) och skapa index.
            #
            # BUGGEN (mätt i drift 2026-09-22): villkoret var
            # `if row and row[0] == 'USER-DEFINED'` — dvs. kolumnen
            # konverterades bara om den REDAN var en vector-typ. En
            # TEXT-kolumn (vilket `fields.Text` skapar) lämnades som text,
            # så `1 - (embedding <=> %s::vector)` kastade
            # `UndefinedFunction: operator does not exist: text <=> vector`.
            # Cirkulär logik: den migreras bara om den redan är migrerad.
            #
            # Följden i drift: /ai/stream svarade HTTP 500 för varje session
            # med >50 rader (de som anropar _summarize_history →
            # _bridge_to_personal_memory → search_for_user), och användaren
            # såg "Anslutningen till AI-servern bröts".
            #
            # Konverteringen är idempotent och tål att köras om: en kolumn
            # som redan är vector lämnas orörd.
            cr.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            if cr.fetchone():
                cr.execute(
                    "SELECT udt_name FROM information_schema.columns "
                    "WHERE table_name = 'ai_personal_memory' "
                    "AND column_name = 'embedding'")
                row = cr.fetchone()
                if row and row[0] != 'vector':
                    # Värden skrivna som text-literal ('[0.1,0.2,...]') är
                    # redan giltiga vector-literaler → casten fungerar.
                    # NULL/otolkbara värden nollställs i stället för att
                    # fälla hela ALTER:en.
                    cr.execute(
                        "ALTER TABLE ai_personal_memory "
                        "ALTER COLUMN embedding TYPE vector(1024) "
                        "USING CASE WHEN embedding IS NULL THEN NULL "
                        "ELSE embedding::vector(1024) END")
                    _logger.info(
                        'ai_personal_memory.embedding: %s → vector(1024)'
                        ' (var %s)', 'vector(1024)', row[0])
                if row and row[0] == 'vector':
                    cr.execute("SELECT 1 FROM pg_indexes WHERE tablename = 'ai_personal_memory' AND indexname = 'idx_ai_personal_memory_embedding'")
                    if not cr.fetchone():
                        cr.execute("CREATE INDEX idx_ai_personal_memory_embedding ON ai_personal_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)")
                        _logger.info('Created ivfflat index on embedding')

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

            cr.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            if cr.fetchone():
                cr.execute(
                    "SELECT udt_name FROM information_schema.columns "
                    "WHERE table_name = 'ai_company_memory' "
                    "AND column_name = 'embedding'")
                row = cr.fetchone()
                # Samma cirkulära villkor som ai_personal_memory hade:
                # `== 'USER-DEFINED'` konverterade bara en kolumn som redan
                # var vector. En TEXT-kolumn lämnades som text →
                # `text <=> vector` kastade UndefinedFunction i drift.
                if row and row[0] != 'vector':
                    cr.execute(
                        "ALTER TABLE ai_company_memory "
                        "ALTER COLUMN embedding TYPE vector(1024) "
                        "USING CASE WHEN embedding IS NULL THEN NULL "
                        "ELSE embedding::vector(1024) END")
                    _logger.info(
                        'ai_company_memory.embedding: → vector(1024) '
                        '(var %s)', row[0])
                if row and row[0] == 'vector':
                    cr.execute("SELECT 1 FROM pg_indexes WHERE tablename = 'ai_company_memory' AND indexname = 'idx_ai_company_memory_embedding'")
                    if not cr.fetchone():
                        cr.execute("CREATE INDEX idx_ai_company_memory_embedding ON ai_company_memory USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)")

            cr.execute("SELECT 1 FROM pg_indexes WHERE tablename = 'ai_company_memory' AND indexname = 'idx_ai_company_memory_company'")
            if not cr.fetchone():
                cr.execute("CREATE INDEX idx_ai_company_memory_company ON ai_company_memory (company_id, create_date DESC)")
    except Exception as e:
        _logger.warning('SQL migration for ai.company.memory failed (non-fatal): %s', e)
        try:
            cr.execute('ROLLBACK TO SAVEPOINT pre_company_memory_sql')
        except Exception:
            cr.rollback()

    # ════════════════════════════════════════════
    # AGE Graph initialization (Odoo Mind)
    # ════════════════════════════════════════════
    cr.execute('SAVEPOINT pre_age_init')
    try:
        cr.execute("SELECT 1 FROM pg_extension WHERE extname = 'age'")
        if cr.fetchone():
            if _age_graph_exists(cr):
                _logger.info('odoo_mind graph already exists')
            else:
                cr.execute("SELECT * FROM ag_catalog.create_graph('odoo_mind')")
                _logger.info('Created odoo_mind graph')
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
            'ai_okf_concept-tabellen finns inte — '
            'okf_ensure_search_infrastructure måste köras efter tabellskapande'
        )

    # 1. search_vector — GENERATED STORED över summary + title (svensk stemming)
    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'ai_okf_concept' AND column_name = 'search_vector'
    """)
    if not cr.fetchone():
        cr.execute("""
            ALTER TABLE ai_okf_concept
            ADD COLUMN search_vector tsvector
            GENERATED ALWAYS AS (
                to_tsvector('swedish',
                            coalesce(summary, '') || ' ' || coalesce(title, ''))
            ) STORED
        """)
        _logger.info('OKF: skapade search_vector på ai_okf_concept'
                     ' (svensk FTS över summary + title)')

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
                    'OKF: satte embedding till vector(1024) — kolumnen var '
                    'dimensionslös sedan migration 1.11 misslyckats tyst')

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
                _logger.info('OKF: skapade ivfflat-index över embedding')

    # 4. B-tree för versionsuppslag (scope, concept_key, version DESC)
    cr.execute("""
        SELECT 1 FROM pg_indexes
        WHERE tablename = 'ai_okf_concept'
          AND indexname = 'idx_ai_okf_concept_scope_key'
    """)
    if not cr.fetchone():
        cr.execute("""
            CREATE INDEX idx_ai_okf_concept_scope_key
            ON ai_okf_concept (scope, concept_key, version DESC)
        """)
        _logger.info('OKF: skapade B-tree-index (scope, concept_key, version)')


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

    # Quest Builder
    if not env['ai.coworker'].search_count([('name', '=', 'Quest Builder')]):
        env['ai.coworker'].create({
            'name': 'Quest Builder',
            'description': BUILDER_SYSTEM_PROMPT,
            'sub_description': 'AI that helps you build and configure quests',
            'init_type': 'manual',
            'status': 'active',
            'show_in_chat': False,
            'is_supervisor': False,
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
            'is_supervisor': False,
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
        if _age_graph_exists(cr):
            _logger.info('odoo_mind graph already exists')
        else:
            cr.execute("SELECT * FROM ag_catalog.create_graph('odoo_mind')")
            _logger.info('Created odoo_mind graph')

        # 3. Create cron_sync_graph if not exists
        cron = env['ir.cron'].search([
            ('name', '=', 'Odoo Mind Graph Sync'),
        ], limit=1)
        if not cron:
            env['ir.cron'].create({
                'name': 'Odoo Mind Graph Sync',
                'model_id': env['ir.model']._get('graph.node.definition').id,
                'state': 'code',
                'code': 'model._sync_all()',
                'interval_number': 5,
                'interval_type': 'minutes',
                'numbercall': -1,
                'active': True,
                'priority': 0,
                'user_id': env.ref('base.user_root').id,
            })
            _logger.info('Created cron: Odoo Mind Graph Sync')

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
            'Ingen aktiv ai.model finns — default-modellen kunde inte sättas. '
            'Agenter utan model_id kan inte köra.')
        return

    env['ir.config_parameter'].sudo().set_param(param, str(model.id))
    _logger.info('Default-modell satt till %s (id=%d)', model.name, model.id)

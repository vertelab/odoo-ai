# -*- coding: utf-8 -*-
"""Post-install hook: create Quest Builder and Skill Builder quests."""

import logging

_logger = logging.getLogger(__name__)


def post_init_hook_personal_memory(env):
    """Post-install hook for ai.personal.memory (Odoo 18 — takes env).
    
    Skapar pgvector-kolumn, tsvector GENERATED COLUMN och index.
    Idempotent — körs endast om tabellen finns och kolumner saknas.
    """
    cr = env.cr
    try:
        # 1. tsvector GENERATED COLUMN
        cr.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'ai_personal_memory'
              AND column_name = 'search_vector'
        """)
        if not cr.fetchone():
            cr.execute("""
                ALTER TABLE ai_personal_memory
                ADD COLUMN search_vector tsvector
                GENERATED ALWAYS AS (
                    to_tsvector('swedish', coalesce(content, ''))
                ) STORED
            """)
            _logger.info('Created search_vector column on ai_personal_memory')
        
        # 2. GIN-index för fulltext-sök
        cr.execute("""
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'ai_personal_memory'
              AND indexname = 'idx_ai_personal_memory_fts'
        """)
        if not cr.fetchone():
            cr.execute("""
                CREATE INDEX idx_ai_personal_memory_fts
                ON ai_personal_memory
                USING GIN(search_vector)
            """)
            _logger.info('Created GIN index on search_vector')
        
        # 3. pgvector-index (endast om kolumnen är av typen vector)
        cr.execute("""
            SELECT 1 FROM pg_extension WHERE extname = 'vector'
        """)
        if cr.fetchone():
            # Kontrollera att embedding-kolumnen är pgvector-typ, inte text
            cr.execute("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'ai_personal_memory'
                  AND column_name = 'embedding'
            """)
            emb_type = cr.fetchone()
            if emb_type and emb_type[0] == 'USER-DEFINED':
                cr.execute("""
                    SELECT 1 FROM pg_indexes
                    WHERE tablename = 'ai_personal_memory'
                      AND indexname = 'idx_ai_personal_memory_embedding'
                """)
                if not cr.fetchone():
                    cr.execute("""
                        CREATE INDEX idx_ai_personal_memory_embedding
                        ON ai_personal_memory
                        USING ivfflat (embedding vector_cosine_ops)
                        WITH (lists = 100)
                    """)
                    _logger.info('Created ivfflat index on embedding')
            else:
                _logger.info('embedding-kolumnen är inte pgvector-typ — hoppar ivfflat-index')
        
        # 4. B-tree-index för user_id
        cr.execute("""
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'ai_personal_memory'
              AND indexname = 'idx_ai_personal_memory_user_id'
        """)
        if not cr.fetchone():
            cr.execute("""
                CREATE INDEX idx_ai_personal_memory_user_id
                ON ai_personal_memory (user_id, create_date DESC)
            """)
            _logger.info('Created B-tree index on user_id')
    except Exception as e:
        _logger.warning('SQL migration for ai.personal.memory failed (non-fatal): %s', e)
    
    # ════════════════════════════════════════════
    # Company Memory SQL
    # ════════════════════════════════════════════
    try:
        cr.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'ai_company_memory'
              AND column_name = 'search_vector'
        """)
        if not cr.fetchone():
            cr.execute("""
                ALTER TABLE ai_company_memory
                ADD COLUMN search_vector tsvector
                GENERATED ALWAYS AS (
                    to_tsvector('swedish', coalesce(content, ''))
                ) STORED
            """)
            _logger.info('Created search_vector on ai_company_memory')
        
        cr.execute("""
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'ai_company_memory'
              AND indexname = 'idx_ai_company_memory_fts'
        """)
        if not cr.fetchone():
            cr.execute("""
                CREATE INDEX idx_ai_company_memory_fts
                ON ai_company_memory USING GIN(search_vector)
            """)
        
        cr.execute("""
            SELECT 1 FROM pg_extension WHERE extname = 'vector'
        """)
        if cr.fetchone():
            cr.execute("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'ai_company_memory'
                  AND column_name = 'embedding'
            """)
            emb_type = cr.fetchone()
            if emb_type and emb_type[0] == 'USER-DEFINED':
                cr.execute("""
                    SELECT 1 FROM pg_indexes
                    WHERE tablename = 'ai_company_memory'
                      AND indexname = 'idx_ai_company_memory_embedding'
                """)
                if not cr.fetchone():
                    cr.execute("""
                        CREATE INDEX idx_ai_company_memory_embedding
                        ON ai_company_memory
                        USING ivfflat (embedding vector_cosine_ops)
                        WITH (lists = 100)
                    """)
            else:
                _logger.info('company_memory embedding är inte pgvector-typ — hoppar ivfflat-index')        
        cr.execute("""
            SELECT 1 FROM pg_indexes
            WHERE tablename = 'ai_company_memory'
              AND indexname = 'idx_ai_company_memory_company'
        """)
        if not cr.fetchone():
            cr.execute("""
                CREATE INDEX idx_ai_company_memory_company
                ON ai_company_memory (company_id, create_date DESC)
            """)
    except Exception as e:
        _logger.warning('SQL migration for ai.company.memory failed (non-fatal): %s', e)

    # ════════════════════════════════════════════
    # AGE Graph initialization (Odoo Mind)
    # ════════════════════════════════════════════
    try:
        # 1. CREATE EXTENSION IF NOT EXISTS
        cr.execute("CREATE EXTENSION IF NOT EXISTS age CASCADE")
        _logger.info('AGE extension ensured')

        # 2. Create graph if not exists
        cr.execute("SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'odoo_mind'")
        if not cr.fetchone():
            cr.execute("SELECT * FROM ag_catalog.create_graph('odoo_mind')")
            _logger.info('Created odoo_mind graph')
        else:
            _logger.info('odoo_mind graph already exists')
    except Exception as e:
        _logger.warning(
            'Odoo Mind graph initialization failed (non-fatal): %s', e)
        _logger.warning(
            'Apache AGE may not be installed. '
            'Run: salt \'*\' state.apply postgres.age')

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
        okf_init_default_artifact_types(env)
        env.flush_all()
        env.cr.commit()
    except Exception as e:
        _logger.warning('Org init failed (non-fatal): %s', e)


def okf_init_default_artifact_types(env):
    """OKF-init: sätt default artifact_type 'learning' på befintliga
    ai.memory-poster som saknar artifact_type_id (task 1.5). Idempotent."""
    try:
        learning = env.ref('ai_agent_core.artifact_type_learning',
                           raise_if_not_found=False)
        if not learning:
            _logger.warning('OKF: learning artifact type saknas — hoppar default')
            return
        memories = env['ai.memory'].search([('artifact_type_id', '=', False)])
        if memories:
            memories.write({'artifact_type_id': learning.id})
            _logger.info('OKF: satte default artifact_type learning på %s poster',
                         len(memories))
    except Exception as e:
        _logger.warning('OKF-init default artifact types failed (non-fatal): %s', e)

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
    """Create default coworker + load templates for the organization layer."""
    import os, json

    # 1. Create default coworker if none exists
    if not env['ai.coworker'].search_count([('is_default', '=', True)]):
        agent = env['ai.agent'].create({
            'name': 'Allmän assistent',
            'ai_role': 'General purpose AI assistant',
            'status': 'active',
        })
        coworker = env['ai.coworker'].create({
            'name': 'Allmän',
            'description': 'Allmän AI-assistent. Hjälper med frågor, '
                          'styr upp organisationen, och tipsar om '
                          'förbättringar via kaizen.',
            'init_type': 'manual',
            'status': 'active',
            'heartbeat_enabled': True,
            'inject_company_memory': True,
            'inject_nudging': True,
            'is_default': True,
        })
        env['ai.coworker.agent'].create({
            'coworker_id': coworker.id,
            'agent_id': agent.id,
            'role': 'lead',
        })
        # Create init types
        InitType = env['ai.coworker.init_type']
        InitType.create({
            'coworker_id': coworker.id,
            'init_type': 'web_ui',
            'active': True,
        })
        InitType.create({
            'coworker_id': coworker.id,
            'init_type': 'cron',
            'active': True,
            'cron_interval_number': 5,
            'cron_interval_type': 'minutes',
        })
        _logger.info('Created default coworker: Allmän')
    else:
        _logger.info('Default coworker already exists — skipping')

    # 2. Load templates from JSON files
    try:
        template_model = env['ai.org.template']
        template_model.load_all_templates()
        _logger.info('Templates loaded')
    except Exception as e:
        _logger.warning('Template loading skipped (non-fatal): %s', e)

    _logger.info('post_init_hook_org complete')


def post_init_hook(env):
    """Create Quest Builder and Skill Builder quests if they don't exist."""

    # Run org init too
    post_init_hook_org(env)

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
        # 1. CREATE EXTENSION IF NOT EXISTS
        cr.execute("CREATE EXTENSION IF NOT EXISTS age CASCADE")
        _logger.info('AGE extension ensured')

        # 2. Create graph if not exists
        cr.execute("SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'odoo_mind'")
        if not cr.fetchone():
            cr.execute("SELECT * FROM ag_catalog.create_graph('odoo_mind')")
            _logger.info('Created odoo_mind graph')
        else:
            _logger.info('odoo_mind graph already exists')

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

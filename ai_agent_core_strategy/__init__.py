# -*- coding: utf-8 -*-
"""Post-install hook: create strategy skills, agents, and quests.

Pattern: same as ai_agent_core_academic — thin module, post_init_hook creates
ai.skill, ai.agent, and ai.quest records. Agents interact with strategy models
via XML-RPC (not Python imports) to avoid circular dependencies.
"""

import logging

_logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# RECIPE TEXTS
# ═══════════════════════════════════════════════════════════════════════════

_RECIPE_BMC = """# Business Model Canvas Generator

Generate a complete 9-block Business Model Canvas from Odoo data.

## Data Sources (via XML-RPC)
- `business.model.canvas.get_live_context()` — customer count, revenue, products
- `res.partner` — customer segments
- `crm.lead` — pipeline and conversion
- `sale.order` — revenue streams
- `product.template` — products → value propositions

## The 9 Blocks
1. **Customer Segments** — who are we creating value for?
2. **Value Propositions** — what problem do we solve?
3. **Channels** — how do we reach customers?
4. **Customer Relationships** — how do we interact?
5. **Revenue Streams** — how do we make money?
6. **Key Resources** — what assets do we need?
7. **Key Activities** — what do we do daily?
8. **Key Partnerships** — who helps us?
9. **Cost Structure** — what does it cost?

## Process
1. Read Odoo context via `get_live_context()`
2. Draft all 9 blocks (right side first, then left)
3. Challenge 3 hidden assumptions
4. Stress-test: what if each assumption is wrong?
5. Write to `business.model.canvas` model fields
6. Set `state='draft'`, then call `action_validate()`"""

_RECIPE_SWOT = """# SWOT Analysis Generator

Generate a comprehensive SWOT analysis from Odoo business data.

## Data Sources
- `business.model.canvas` — current business model
- `crm.lead` — market opportunities and threats
- `sale.order` — revenue strengths and weaknesses
- `strategy.risk` — existing risk register

## Framework
- **Strengths**: Internal positive factors (resources, capabilities, advantages)
- **Weaknesses**: Internal negative factors (gaps, limitations, vulnerabilities)
- **Opportunities**: External positive factors (market trends, gaps, technologies)
- **Threats**: External negative factors (competition, regulation, market shifts)

## Process
1. Analyze company data for internal factors (strengths/weaknesses)
2. Analyze market data for external factors (opportunities/threats)
3. Cross-reference: S-O strategies, W-O improvements, S-T defenses, W-T mitigations
4. Write to `swot.analysis` model fields
5. Link to related `strategy.risk` records"""

_RECIPE_VPC = """# Value Proposition Canvas Generator

Create a Value Proposition Canvas mapping customer jobs, pains, and gains to products and services.

## Data Sources
- `business.model.canvas` — customer segments and value propositions
- `res.partner` — customer profiles and feedback
- `crm.lead` — customer pain points from sales conversations

## Framework
- **Customer Profile**: Jobs-to-be-done, Pains, Gains
- **Value Map**: Products & Services, Pain Relievers, Gain Creators
- **Fit**: Verify that value map addresses customer profile

## Process
1. Extract customer segments from BMC
2. Map jobs, pains, gains per segment
3. Map products to pain relievers and gain creators
4. Verify fit and identify gaps
5. Write to `value.proposition.canvas` model"""

_RECIPE_OKR = """# OKR Framework Generator

Generate Objectives and Key Results from strategy data.

## Data Sources
- `strategy.plan` — strategic context and timeframe
- `strategy.vision` — mission, vision, values
- `business.model.canvas` — business priorities
- `swot.analysis` — strategic insights

## Framework
- **Objectives**: Qualitative, inspirational, time-bound goals
- **Key Results**: Quantitative, measurable outcomes (2-5 per objective)
- **Initiatives**: Projects that drive key results

## Process
1. Read strategic context (vision, BMC, SWOT)
2. Draft 3-5 objectives aligned with strategy
3. For each objective, define 2-5 measurable key results
4. Link to `strategy.initiative` records
5. Write to `okr.objective` and `okr.key.result` models"""

_RECIPE_PORTER = """# Porter's Five Forces Analysis

Analyze industry competitiveness using Porter's Five Forces framework.

## Framework
1. **Competitive Rivalry** — intensity of competition in the industry
2. **Threat of New Entrants** — barriers to entry
3. **Bargaining Power of Suppliers** — supplier concentration and alternatives
4. **Bargaining Power of Buyers** — buyer concentration and switching costs
5. **Threat of Substitutes** — alternative solutions

## Data Sources
- `res.partner` — competitor and supplier data
- `crm.lead` — lost opportunities (why customers chose alternatives)
- `sale.order` — pricing and volume data

## Process
1. Analyze each force with data where available
2. Rate each force: Low / Medium / High
3. Identify strategic implications
4. Write analysis to `strategy.plan.description` or linked document"""

_RECIPE_BLUE_OCEAN = """# Blue Ocean Strategy Canvas

Apply Blue Ocean Strategy to find uncontested market space.

## Framework
- **Strategy Canvas**: Visualize current competitive factors
- **Four Actions**: Eliminate, Reduce, Raise, Create
- **ERRC Grid**: Map actions to competitive factors

## Process
1. Identify industry competitive factors
2. Map current offering on strategy canvas
3. Apply four actions framework
4. Create new value curve
5. Write results to `strategy.plan` or linked document"""

_RECIPE_BCG = """# BCG Growth-Share Matrix

Apply BCG matrix to analyze product/service portfolio.

## Framework
- **Stars**: High growth, high market share — invest
- **Cash Cows**: Low growth, high market share — milk
- **Question Marks**: High growth, low market share — analyze
- **Dogs**: Low growth, low market share — divest

## Data Sources
- `product.template` — product portfolio
- `sale.order` — revenue by product, growth trends

## Process
1. Calculate relative market share and market growth rate per product
2. Plot products on BCG matrix
3. Recommend strategic actions per quadrant
4. Write analysis to linked document"""

_RECIPE_ANSOFF = """# Ansoff Matrix

Apply Ansoff Matrix for growth strategy planning.

## Framework
- **Market Penetration**: Existing markets × Existing products
- **Market Development**: New markets × Existing products
- **Product Development**: Existing markets × New products
- **Diversification**: New markets × New products

## Process
1. Analyze current product-market position
2. Identify growth opportunities in each quadrant
3. Assess risk level per quadrant
4. Recommend growth strategy
5. Write to `strategy.plan` or linked document"""

_RECIPE_RACI = """# RACI Matrix Generator

Create RACI responsibility matrices for strategic initiatives.

## Framework
- **R**esponsible: Who does the work?
- **A**ccountable: Who approves?
- **C**onsulted: Who provides input?
- **I**nformed: Who needs to know?

## Data Sources
- `strategy.initiative` — strategic initiatives
- `res.users` — team members and roles

## Process
1. List strategic initiatives as rows
2. List team members/roles as columns
3. Assign R, A, C, I per cell
4. Verify: exactly one A per row, at least one R per row
5. Write to initiative documentation"""

_RECIPE_RISK_MATRIX = """# Risk Matrix

Map strategic risks on a probability × impact matrix.

## Framework
- **Probability**: Rare, Unlikely, Possible, Likely, Almost Certain
- **Impact**: Negligible, Minor, Moderate, Major, Catastrophic
- **Risk Score**: Probability × Impact (1-25)

## Data Sources
- `strategy.risk` — existing risk register
- `swot.analysis` — threat identification
- `strategy.initiative` — initiative dependencies

## Process
1. Identify strategic risks from SWOT and initiatives
2. Assess probability and impact for each risk
3. Plot on 5×5 risk matrix
4. Define mitigation strategies for top risks
5. Create/update `strategy.risk` records"""

_RECIPE_VALUE_CHAIN = """# Value Chain Analysis

Analyze the company's value chain to identify competitive advantages.

## Framework (Porter's Value Chain)
**Primary Activities**: Inbound Logistics, Operations, Outbound Logistics, Marketing & Sales, Service
**Support Activities**: Firm Infrastructure, HR Management, Technology Development, Procurement

## Data Sources
- `product.template` — product data
- `sale.order` — customer and revenue data
- `res.partner` — supplier and partner data

## Process
1. Map current value chain activities
2. Identify cost drivers and value drivers per activity
3. Find opportunities for differentiation or cost advantage
4. Write analysis to `strategy.plan` or linked document"""

_RECIPE_FINANCIAL = """# Financial Forecast Generator

Generate financial forecasts from BMC data with scenario modeling.

## Data Sources
- `business.model.canvas` — revenue_streams, cost_structure
- `sale.order` — historical revenue
- `account.move.line` — historical costs

## Process
1. **Baseline**: Monthly revenue/cost projections for 12-36 months
2. **Validate**: Compare against historical data
3. **Scenarios** (4-6):
   - Optimistic (+20% revenue, -10% costs)
   - Pessimistic (-20% revenue, +15% costs)
   - Growth (5x over 3 years)
   - Market Shift (external disruption)
4. **Metrics**: Break-even date, profit margin, growth rate
5. Write to `strategy.forecast` model (if strategy_finance installed) or `strategy.plan.description`"""

_RECIPE_MECE = """# MECE Issue Tree

Build MECE (Mutually Exclusive, Collectively Exhaustive) issue trees for strategic problem decomposition.

## Framework
- **Mutually Exclusive**: No overlap between branches
- **Collectively Exhaustive**: All possibilities covered

## Process
1. Define the core strategic question
2. Decompose into 3-5 MECE branches
3. Further decompose each branch
4. Identify data needs per leaf node
5. Write structured tree to linked document"""

_RECIPE_HYPOTHESIS = """# Hypothesis Tree

Build hypothesis trees for structured strategic problem-solving.

## Framework
- Core hypothesis → Sub-hypotheses → Evidence required
- Each branch is falsifiable
- Evidence drives iteration

## Process
1. Formulate core strategic hypothesis
2. Break into 3-5 sub-hypotheses
3. Define evidence needed per sub-hypothesis
4. Prioritize by impact and testability
5. Write structured tree to linked document"""

_RECIPE_ROOT_CAUSE = """# Root Cause Analysis

Perform root cause analysis using 5 Whys and Fishbone diagrams.

## Frameworks
- **5 Whys**: Iterative why-questioning to find root cause
- **Fishbone (Ishikawa)**: Categories: People, Process, Technology, Materials, Environment, Management

## Process
1. Define the problem clearly
2. Apply 5 Whys to trace to root cause
3. Map contributing factors on fishbone diagram
4. Identify corrective actions
5. Write analysis and link to `strategy.action` records"""

_RECIPE_TAM_SAM_SOM = """# TAM/SAM/SOM Analysis

Calculate Total Addressable Market, Serviceable Addressable Market, and Serviceable Obtainable Market.

## Framework
- **TAM**: Total market demand (top-down or bottom-up)
- **SAM**: Market you can reach with your business model
- **SOM**: Market you can realistically capture

## Data Sources
- `res.partner` — market and customer data
- `sale.order` — current revenue for bottom-up estimates

## Process
1. Calculate TAM using top-down (industry reports) and bottom-up (unit economics)
2. Narrow to SAM based on geography, segment, channel
3. Estimate SOM based on competitive position and capacity
4. Write to `strategy.plan` or linked financial document"""

_RECIPE_UNIT_ECONOMICS = """# Unit Economics Analysis

Calculate unit economics: CAC, LTV, payback period, contribution margin.

## Key Metrics
- **CAC** (Customer Acquisition Cost): Marketing spend / New customers
- **LTV** (Lifetime Value): Avg revenue per customer × Avg customer lifetime
- **LTV:CAC Ratio**: Target > 3:1
- **Payback Period**: CAC / Monthly gross margin per customer
- **Contribution Margin**: Revenue - Variable costs

## Data Sources
- `sale.order` — revenue per customer
- `res.partner` — customer lifetime and churn

## Process
1. Calculate key metrics from Odoo data
2. Benchmark against industry standards
3. Identify improvement levers
4. Write to linked financial document"""

_RECIPE_COST_PLUS = """# Cost-Plus Pricing

Apply cost-plus pricing methodology to products and services.

## Framework
- **Full Cost**: Direct costs + allocated overhead
- **Markup**: Target margin percentage
- **Price**: Full Cost × (1 + Markup)

## Data Sources
- `product.template` — product costs and prices
- `account.move.line` — cost data

## Process
1. Calculate full cost per product/service
2. Apply target markup
3. Compare to market prices
4. Recommend pricing adjustments
5. Write analysis to linked document"""

_RECIPE_VALUE_BASED = """# Value-Based Pricing

Apply value-based pricing methodology — price based on customer perceived value.

## Framework
- **Economic Value**: Cost savings + revenue increase for customer
- **Reference Price**: Next best alternative
- **Differentiation Value**: Your advantage over reference
- **Price Range**: Reference Price to Reference + Differentiation

## Data Sources
- `sale.order` — historical pricing and win/loss data
- `crm.lead` — competitive intelligence

## Process
1. Quantify economic value to customer
2. Identify reference alternatives
3. Calculate differentiation value
4. Recommend price range
5. Write analysis to linked document"""

_RECIPE_FREEMIUM = """# Freemium Packaging

Define freemium pricing tiers and conversion paths.

## Framework
- **Free Tier**: Core value, viral features
- **Premium Tiers**: Advanced features, capacity, support
- **Conversion Path**: Free → Premium triggers

## Process
1. Define core value for free tier
2. Design premium feature tiers
3. Identify conversion triggers
4. Calculate conversion rate targets
5. Write pricing strategy to linked document"""

_RECIPE_MEETING_PREP = """# Meeting Preparation

Prepare a strategic meeting agenda with AI-generated briefs per agenda item.
Supports department-aware reporting and behavioral nudge design.

## Data Sources (via XML-RPC)
- `strategy.meeting` — the meeting record with template and type
- `strategy.meeting.agenda.item` — agenda items with sequence, duration,
  `item_type`, `department_id`, `report_status`
- `strategy.plan` — related strategic plan
- `okr.objective` / `okr.key.result` — current OKR progress.
  Filter by `department_id` when agenda item has one.
- `strategy.risk` — recent risk changes
- `strategy.department.report` — this week's approved department reports
- `marketing.world.brief` — latest world monitoring brief (if installed)
- `strategy.meeting.decision` — previous meeting's open decisions

## Process

### Step 1: Read Meeting Context
Read the meeting record to understand:
- `meeting_type`: 'board' (formal, governance-focused) or 'management' (action-oriented)
- `template_id`: which template was applied
- `plan_id`: strategic context

### Step 2: Process Each Agenda Item

#### A. Department Report Items (`item_type='department_report'`)
When `department_id` is set on the agenda item:
1. Fetch `okr.objective` WHERE `department_id` = agenda_item's department
2. For each OKR, call `_get_salience_level()` to determine:
   - 🔴 `critical` (<60% progress): flags "Kräver beslut idag"
   - ⚠️ `warning` (60-80%): flags "Följer planen med viss risk"
   - ✅ `ok` (>80%): flags "I fas enligt plan"
3. Fetch `strategy.department.report` for this department + this week
4. Fetch kaizen findings linked to this department's quests
5. Generate content following the **Department Report Structure** below
6. Write to `agenda_item.notes` as HTML
7. Set `report_status='draft'` and `ai_generated=True`

#### B. Strategic Status Items (`item_type='strategic_status'`)
For board meetings — CEO's aggregated view:
1. Fetch all strategic OKRs (`department_id=NULL`)
2. For each strategic OKR, find department OKRs via `parent_id`
3. Aggregate progress from department OKRs (weighted average)
4. Fetch all approved `strategy.department.report` for this week
5. Generate a CEO brief with:
   - Per strategic OKR: aggregated progress + department contributions
   - Red/yellow/green per department
   - Recommendations for board decisions

#### C. Standard Items
Fall back to generic meeting preparation as before.

### Step 3: After Generating
1. Post a summary message to the meeting chatter
2. If `department_report` items: notify the department head
   (via `department_id.manager_id.user_id`)

## Department Report Structure

For `department_report` items, write content to `agenda_item.notes` as HTML
following this exact structure:

```html
<h2>Lägesrapport {department.name} — v{week_number}</h2>

<h3>1. OKR-status</h3>
<ul>
  <li>{salience_icon} <strong>{okr.name}</strong>: {okr.progress}%
    — {status_text}</li>
  <!-- repeat for each OKR -->
</ul>

<h3>2. Avvikelser och trender</h3>
<ul>
  <li>Öppna avvikelser: {count}</li>
  <li>Helpdesk-trend: {trend_description}</li>
</ul>

<h3>3. Personalsignaler</h3>
<ul>
  <li>{signal_1}</li>
  <li>{signal_2}</li>
</ul>

<h3>4. Rekommendationer</h3>
<ul>
  <li>{recommendation_1}</li>
  <li>{recommendation_2}</li>
</ul>

<p><em>AI-genererat utkast — granska och redigera före mötet.</em></p>
```

## Friction Reduction — Pre-filled Decision Proposals

When an OKR is below 60% (salience = `critical`), automatically generate
a decision proposal. Apply behavioral design principles:
- **Default**: Pre-fill the decision, user can edit or reject
- **Transparency**: Always mark as AI-generated
- **Opt-out**: Always include the option to maintain current plan

Write the proposal into the agenda item's `notes`:

```html
<div class="ai-decision-proposal" style="border-left: 3px solid #dc3545;
     padding: 10px; margin: 10px 0; background: #fff5f5;">
  <strong>🤖 AI-förslag till beslut:</strong>
  <p>OKR "<em>{okr.name}</em>" är {okr.progress}% med
  {weeks_remaining} veckor kvar till deadline.
  Föreslås att ledningsgruppen beslutar att:</p>
  <ul>
    <li>☐ {suggested_action_1}</li>
    <li>☐ {suggested_action_2}</li>
    <li>☐ Behåll nuvarande plan men acceptera risk för försening</li>
  </ul>
  <p><em>Detta är ett AI-genererat utkast. Redigera, förkasta,
  eller be AI:n om ett nytt förslag.</em></p>
</div>
```

Suggested actions should be specific, actionable, and connected to the
OKR's key results. Use data from kaizen findings and department signals
to ground the suggestions in evidence.

## Nudge Design Principles (from behavioral-design skill)

When generating meeting content, apply these principles:
1. **Salience**: Critical items first. 🔴 before ⚠️ before ✅.
2. **Defaults**: Pre-fill, don't leave blanks. The department head edits, not creates.
3. **Social proof**: If other departments have already reviewed, mention it.
4. **Friction reduction**: Every decision proposal is one click away from approval.
5. **Implementation intentions**: "After the meeting, [person] will [action] by [deadline]."
"""

_RECIPE_MEETING_MINUTES = """# Meeting Minutes Generator

Generate structured meeting minutes from a completed meeting.

## Data Sources (via XML-RPC)
- `strategy.meeting` — the meeting record
- `strategy.meeting.agenda.item` — items with notes and state
- `strategy.meeting.decision` — decisions made during meeting
- `strategy.meeting.participant` — attendance and roles

## Process
1. Read meeting agenda with all items
2. For each completed agenda item, generate:
   - Summary of discussion (from notes)
   - Decisions made (from decisions linked to this item)
   - Action items (extracted from discussion)
3. Aggregate:
   - List of all decisions with status
   - List of all action items with suggested owners and deadlines
   - Risk changes based on decisions
4. Write full minutes to `minutes_draft` field
5. Create any missing `strategy.meeting.decision` records

## Output Format
```
Meeting Minutes — {meeting.name}
Date: {meeting.date}

Participants:
- {name} ({role}) — ✓ attended

1. {agenda_item_1}
   Discussion: ...
   Decision: ...
   Action: {owner}, deadline {date}

...
```"""

_RECIPE_BOARD_SECRETARY = """# Board Secretary — Governance & Follow-up

Monitor open decisions, governance compliance, and prepare follow-up.

## Data Sources
- `strategy.meeting.decision` — decisions with state=approved
- `strategy.meeting` — recent meetings
- `strategy.risk` — open risks

## Process (Daily Check)
1. Find all decisions with `state='approved'` older than 30 days
2. For each, check if a linked strategy record (risk, OKR, initiative) shows completion
3. If no completion detected:
   - Flag as "pending" for next meeting agenda
   - Suggest escalation to meeting organizer
4. Find decisions with `state='deferred'` — suggest re-discussion
5. Check governance:
   - Are there board meetings without completed minutes?
   - Are there upcoming deadlines (board term end, risk review)?

## Output
- List of pending items for next meeting prep
- Governance reminders"""

_RECIPE_BEHAVIORAL_DESIGN = """# Behavioral Design & Nudge Design

Apply behavioral science to design choice architecture that guides users
toward strategic goals without restricting freedom of choice.

## Theoretical Foundation
- **Thaler & Sunstein (2008)**: Libertarian paternalism — design environments
  so people make better choices, always with opt-out.
- **Kahneman (2011)**: System 1 (fast, automatic) vs System 2 (slow, deliberate).
  Nudges work by targeting System 1 — making the right choice the easy choice.
- **Fogg (2019)**: B=MAP — Behavior = Motivation × Ability × Prompt.
  Focus on Ability (reduce friction) and Prompt (trigger at the right moment).
  Motivation is hardest to change.

## Five Nudge Techniques

### 1. Defaults (Johnson & Goldstein, 2003)
Pre-fill forms, reports, and decisions with the desired outcome.
Users can change it, but the default becomes the path of least resistance.
- Example: Pre-fill meeting agenda with OKR status updates
- When to use: High-effort tasks, compliance, routine reports

### 2. Social Proof (Cialdini, 2006)
Show what others are doing. Descriptive norms ("85% have already done X")
are more effective than injunctive norms ("you should do X").
- Example: "3 of 4 department heads have reviewed their reports"
- When to use: Tasks where peer behavior is visible and relevant

### 3. Salience (Kahneman & Tversky, 1979)
Make important information visually prominent. Loss framing
is approximately 2× more powerful than gain framing.
- Example: OKR <60% → 🔴 "Requires decision today"
- When to use: Critical deadlines, risk indicators, decision points

### 4. Friction Reduction (Fogg, 2009)
Remove steps between intention and action. Every click matters.
- Example: AI-generated report draft instead of blank page
- When to use: Complex tasks, knowledge work, creative output

### 5. Implementation Intentions (Gollwitzer, 1999)
"If-then" plans: "When X happens, I will do Y."
Dramatically increases follow-through by pre-committing to a trigger.
- Example: "When it's Friday 09:00, check your OKR progress"
- When to use: Recurring tasks, goal pursuit, habit formation

## Nudge Design Process

1. **Identify the gap**: What behavior should change? Measure current state.
2. **Diagnose the barrier**: Is it Motivation, Ability, or Prompt? (Fogg's B=MAP)
3. **Select technique**: Match the barrier to the right nudge type.
4. **Design the nudge**: Write the specific message, default, or prompt.
5. **Measure effect**: Did behavior change? Log opened, converted, trend.
6. **Iterate**: If conversion <20% for 2 weeks, switch nudge type.

## Ethics Guidelines (Sunstein, 2014 — Publicity Principle)

- **Transparency**: Every nudge must be labeled as AI-generated.
- **Opt-out**: Always provide a clear way to decline or dismiss.
- **Proportionality**: Match nudge intensity to situation severity.
- **Publicity test**: Would you be comfortable seeing this nudge on the front page?
- **No dark patterns**: Never trick, deceive, or hide the opt-out.

## Nudge Ladder (Escalation Pattern)

Day 0: Default — pre-fill desired outcome
Day 3: Prompt — gentle reminder in personal chat
Day 7: Social proof — "X% have already done this"
Day 14: Default + Salience — auto-add to next meeting agenda
Day 30: Escalation — notify department head + meeting chair

Each step includes opt-out: "Pågår — påminn mig senare."

## Integration with Strategy Nudge Engine

- `strategy.department.report` — weekly reports with review workflow
- `strategy.meeting.decision` — decisions with nudge_level, nudge_opt_out
- `okr.objective._get_salience_level()` — critical/warning/ok color coding
- `strategy.meeting.decision.cron_nudge_ladder()` — daily escalation
- `ai.kaizen.report.nudge_metrics` — weekly aggregation per department"""

_RECIPE_ODOO_CONTEXT = """# Odoo Strategy Context

Connect to Odoo strategy tools via XML-RPC. All strategy data lives in Odoo models.
Read and write exclusively through XML-RPC — never filesystem.

## Core Strategy Models (Read & Write)
- `business.model.canvas` — 9-block BMC, get_live_context()
- `swot.analysis` — strengths, weaknesses, opportunities, threats
- `value.proposition.canvas` — customer profile + value map
- `okr.objective` / `okr.key.result` — objectives and key results
- `strategy.plan` — top-level strategic plan (aggregates all artifacts)
- `strategy.vision` — vision, mission, values, BHAG
- `strategy.initiative` — strategic initiatives with budget
- `strategy.risk` — risk register with probability/impact
- `strategy.action` — strategic action items

## Base Data (Read Only)
- `res.partner`, `crm.lead`, `sale.order`, `account.move.line`

## Security
- Read-only on base data
- Read & Write on strategy models
- Never modify user passwords, company settings, or accounting data"""

# ═══════════════════════════════════════════════════════════════════════════
# SKILL METADATA
# ═══════════════════════════════════════════════════════════════════════════

GITHUB_BASE = 'https://github.com/vertelab/odoo-strategy/blob/18.0/strategy_ai/static/skills'

_SKILLS = [
    # ── Core strategy frameworks ──
    ('business-model-canvas', 'Business Model Canvas',
     'Generate, iterate, and stress-test a 9-block Business Model Canvas from Odoo data.',
     'general', 'BMC, business model canvas, affärsmodell, business model, revenue model',
     _RECIPE_BMC, f'{GITHUB_BASE}/business-model-canvas/SKILL.md'),

    ('swot-analysis', 'SWOT Analysis',
     'Generate a SWOT analysis (Strengths, Weaknesses, Opportunities, Threats) from Odoo business data.',
     'analysis', 'SWOT, strengths weaknesses, swot analysis, strategic analysis',
     _RECIPE_SWOT, f'{GITHUB_BASE}/swot-analysis/SKILL.md'),

    ('value-proposition-canvas', 'Value Proposition Canvas',
     'Create a Value Proposition Canvas mapping customer jobs, pains, gains to products.',
     'general', 'VPC, value proposition canvas, customer profile, value map, jobs to be done',
     _RECIPE_VPC, f'{GITHUB_BASE}/value-proposition-canvas/SKILL.md'),

    ('okr-framework', 'OKR Framework',
     'Generate Objectives and Key Results aligned with strategy.',
     'general', 'OKR, objectives and key results, goal setting, strategic objectives',
     _RECIPE_OKR, f'{GITHUB_BASE}/okr-framework/SKILL.md'),

    ('porters-five-forces', "Porter's Five Forces",
     'Analyze industry competitiveness using Porter\'s Five Forces framework.',
     'analysis', "Porter, five forces, competitive forces, industry analysis, barriers to entry",
     _RECIPE_PORTER, f'{GITHUB_BASE}/porters-five-forces/SKILL.md'),

    ('blue-ocean-strategy', 'Blue Ocean Strategy',
     'Apply Blue Ocean Strategy to find uncontested market space.',
     'analysis', 'blue ocean, strategy canvas, ERRC, value innovation, market creation',
     _RECIPE_BLUE_OCEAN, f'{GITHUB_BASE}/blue-ocean-strategy/SKILL.md'),

    ('bcg-growth-share-matrix', 'BCG Growth-Share Matrix',
     'Analyze product/service portfolio using BCG matrix (Stars, Cash Cows, Question Marks, Dogs).',
     'analysis', 'BCG, growth share matrix, portfolio analysis, product portfolio',
     _RECIPE_BCG, f'{GITHUB_BASE}/bcg-growth-share-matrix/SKILL.md'),

    ('ansoff-matrix', 'Ansoff Matrix',
     'Apply Ansoff Matrix for growth strategy: Market Penetration, Development, Product Development, Diversification.',
     'analysis', 'Ansoff, growth strategy, market penetration, diversification, product development',
     _RECIPE_ANSOFF, f'{GITHUB_BASE}/ansoff-matrix/SKILL.md'),

    ('raci-matrix', 'RACI Matrix',
     'Create RACI responsibility matrices for strategic initiatives.',
     'general', 'RACI, responsibility matrix, roles, accountability, ARCI',
     _RECIPE_RACI, f'{GITHUB_BASE}/raci-matrix/SKILL.md'),

    ('risk-matrix', 'Risk Matrix',
     'Map strategic risks on probability × impact matrix and generate mitigation strategies.',
     'analysis', 'risk matrix, risk assessment, probability impact, risk heatmap, strategic risks',
     _RECIPE_RISK_MATRIX, f'{GITHUB_BASE}/risk-matrix/SKILL.md'),

    ('value-chain-analysis', 'Value Chain Analysis',
     'Analyze the company value chain to identify competitive advantages.',
     'analysis', 'value chain, Porter value chain, primary activities, support activities',
     _RECIPE_VALUE_CHAIN, f'{GITHUB_BASE}/value-chain-analysis/SKILL.md'),

    # ── Financial & analytical frameworks ──
    ('financial-forecast', 'Financial Forecast',
     'Generate financial forecasts from BMC data with scenario modeling.',
     'accounting', 'financial forecast, revenue projection, break-even, what-if scenario, financial model',
     _RECIPE_FINANCIAL, f'{GITHUB_BASE}/financial-forecast/SKILL.md'),

    ('mece-issue-tree', 'MECE Issue Tree',
     'Build MECE (Mutually Exclusive, Collectively Exhaustive) issue trees for strategic problem decomposition.',
     'analysis', 'MECE, issue tree, problem decomposition, structured thinking',
     _RECIPE_MECE, f'{GITHUB_BASE}/mece-issue-tree/SKILL.md'),

    ('hypothesis-tree', 'Hypothesis Tree',
     'Build hypothesis trees for structured strategic problem-solving with falsifiable branches.',
     'analysis', 'hypothesis tree, hypothesis driven, strategic problem solving',
     _RECIPE_HYPOTHESIS, f'{GITHUB_BASE}/hypothesis-tree/SKILL.md'),

    ('root-cause-analysis', 'Root Cause Analysis',
     'Perform root cause analysis using 5 Whys and Fishbone (Ishikawa) diagrams.',
     'analysis', 'root cause, 5 whys, fishbone, Ishikawa, problem analysis',
     _RECIPE_ROOT_CAUSE, f'{GITHUB_BASE}/root-cause-analysis/SKILL.md'),

    ('tam-sam-som', 'TAM/SAM/SOM',
     'Calculate Total Addressable Market, Serviceable Addressable Market, and Serviceable Obtainable Market.',
     'accounting', 'TAM, SAM, SOM, market sizing, addressable market, market analysis',
     _RECIPE_TAM_SAM_SOM, f'{GITHUB_BASE}/tam-sam-som/SKILL.md'),

    ('unit-economics', 'Unit Economics',
     'Calculate unit economics: CAC, LTV, payback period, contribution margin.',
     'accounting', 'unit economics, CAC, LTV, customer acquisition cost, lifetime value',
     _RECIPE_UNIT_ECONOMICS, f'{GITHUB_BASE}/unit-economics/SKILL.md'),

    ('cost-plus-pricing', 'Cost-Plus Pricing',
     'Apply cost-plus pricing methodology to products and services.',
     'accounting', 'cost-plus, pricing, markup, full cost, cost based pricing',
     _RECIPE_COST_PLUS, f'{GITHUB_BASE}/cost-plus-pricing/SKILL.md'),

    ('value-based-pricing', 'Value-Based Pricing',
     'Apply value-based pricing — price based on customer perceived value and differentiation.',
     'accounting', 'value-based pricing, value pricing, economic value, differentiation value',
     _RECIPE_VALUE_BASED, f'{GITHUB_BASE}/value-based-pricing/SKILL.md'),

    ('freemium-packaging', 'Freemium Packaging',
     'Define freemium pricing tiers, features, and conversion paths.',
     'general', 'freemium, pricing tiers, free trial, premium, conversion funnel',
     _RECIPE_FREEMIUM, f'{GITHUB_BASE}/freemium-packaging/SKILL.md'),

    # ── Meetings ──
    ('meeting-prep', 'Meeting Preparation',
     'Prepare a strategic meeting agenda with AI-generated briefs per agenda item.',
     'general', 'meeting, agenda, board meeting, management meeting, mötesförberedelse, dagordning',
     _RECIPE_MEETING_PREP, ''),

    ('meeting-minutes', 'Meeting Minutes',
     'Generate structured meeting minutes from a completed meeting with decisions and actions.',
     'general', 'minutes, protokoll, meeting notes, board minutes, styrelseprotokoll',
     _RECIPE_MEETING_MINUTES, ''),

    ('board-secretary', 'Board Secretary',
     'Monitor open decisions, governance compliance, and prepare meeting follow-up.',
     'general', 'board secretary, governance, decision tracking, styrelsesekreterare, beslutsuppföljning',
     _RECIPE_BOARD_SECRETARY, ''),

    # ── Behavioral design ──
    ('behavioral-design', 'Behavioral Design',
     'Apply behavioral science and nudge design to guide users toward strategic goals. '
     'Covers defaults, social proof, salience, friction reduction, and implementation intentions.',
     'general', 'nudge, behavioral design, beteendedesign, choice architecture, nudging, valarkitektur',
     _RECIPE_BEHAVIORAL_DESIGN, ''),

    # ── Meta ──
    ('odoo-strategy-context', 'Odoo Strategy Context',
     'Connect to Odoo strategy tools via XML-RPC. Provides model references and security constraints.',
     'general', 'odoo context, xml-rpc, strategy models, odoo connection',
     _RECIPE_ODOO_CONTEXT, f'{GITHUB_BASE}/odoo-strategy-context/SKILL.md'),
]

# ═══════════════════════════════════════════════════════════════════════════
# AGENTS
# ═══════════════════════════════════════════════════════════════════════════

_AGENTS = [
    ('agent_strategist', 'Strategist',
     'Generates core strategy artifacts: BMC, VPC, SWOT, Porter, Blue Ocean. '
     'Fast, broad, creative — the idea engine.',
     'cerebras/gpt-oss-120b',
     ['business-model-canvas', 'value-proposition-canvas', 'swot-analysis',
      'porters-five-forces', 'blue-ocean-strategy']),

    ('agent_analyst', 'Analyst',
     'Applies analytical frameworks: MECE, Hypothesis Tree, Root Cause, '
     'Risk Matrix, BCG, Ansoff. Structured, rigorous, evidence-driven.',
     'cerebras/gpt-oss-120b',
     ['mece-issue-tree', 'hypothesis-tree', 'root-cause-analysis',
      'risk-matrix', 'bcg-growth-share-matrix', 'ansoff-matrix']),

    ('agent_finance', 'Financial Analyst',
     'Handles numbers: Financial Forecast, TAM/SAM/SOM, Unit Economics, '
     'Cost-Plus Pricing, Value-Based Pricing, Freemium. Quantitative, precise.',
     'cerebras/gpt-oss-120b',
     ['financial-forecast', 'tam-sam-som', 'unit-economics',
      'cost-plus-pricing', 'value-based-pricing', 'freemium-packaging']),

    ('agent_writer', 'Strategy Writer',
     'Writes and structures: odoo-strategy-context, OKR, RACI, Value Chain. '
     'Produces polished, reader-ready output. Language-aware.',
     'cerebras/gpt-oss-120b',
     ['odoo-strategy-context', 'okr-framework', 'raci-matrix',
      'value-chain-analysis']),

    ('agent_board_secretary', 'Board Secretary',
     'Prepares meetings, generates minutes, and tracks governance compliance. '
     'Handles board and management meeting lifecycles with AI assistance.',
     'anthropic/claude-sonnet-4',
     ['meeting-prep', 'meeting-minutes', 'board-secretary', 'behavioral-design']),

    ('agent_executor', 'Executor',
     'Supervisor fan-out target. Orchestrates multi-skill strategy generation. '
     'Synthesizes outputs from all agents into coherent business plans. '
     'Uses Claude for superior reasoning and synthesis.',
     'anthropic/claude-sonnet-4',
     ['business-model-canvas', 'swot-analysis', 'value-proposition-canvas',
      'okr-framework', 'porters-five-forces', 'blue-ocean-strategy',
      'bcg-growth-share-matrix', 'ansoff-matrix', 'raci-matrix',
      'risk-matrix', 'value-chain-analysis', 'financial-forecast',
      'mece-issue-tree', 'hypothesis-tree', 'root-cause-analysis',
      'tam-sam-som', 'unit-economics', 'cost-plus-pricing',
      'value-based-pricing', 'freemium-packaging', 'odoo-strategy-context',
      'meeting-prep', 'meeting-minutes', 'board-secretary', 'behavioral-design']),
]


# ═══════════════════════════════════════════════════════════════════════════
# POST-INSTALL HOOK
# ═══════════════════════════════════════════════════════════════════════════

def post_init_hook(env):
    """Create skills, agents, and quests after module installation."""

    # ── Check idempotency ──
    existing = env['ai.skill'].search_count([
        ('name', '=', 'Business Model Canvas'),
    ])
    if existing:
        _logger.info('Strategy skills already exist — skipping creation')
        return

    # ── 1. Create skills ──
    _logger.info('Creating %d strategy skills...', len(_SKILLS))
    skill_map = {}
    for xmlid, name, desc, category, keywords, recipe, github_url in _SKILLS:
        skill = env['ai.skill'].create({
            'name': name,
            'description': desc[:1024],
            'category': category,
            'compatibility': 'any',
            'trigger_keywords': keywords,
            'recipe_text': recipe,
            'source_type': 'github',
            'github_url': github_url,
        })
        skill_map[xmlid] = skill

    _logger.info('Created %d skills', len(skill_map))

    # ── 2. Create agents ──
    _logger.info('Creating %d strategy agents...', len(_AGENTS))
    agent_map = {}
    for xmlid, name, desc, model, skill_xmlids in _AGENTS:
        agent = env['ai.agent'].create({
            'name': name,
            'description': desc,
            'provider_type': 'bifrost',
            'bifrost_model': model,
            'status': 'active',
            'skill_ids': [(6, 0, [skill_map[s].id for s in skill_xmlids])],
        })
        agent_map[xmlid] = agent

    _logger.info('Created %d agents', len(agent_map))

    # ── 3. Resolve model IDs for quest powerbox targets ──
    strategy_models = env['ir.model'].search([
        ('model', 'in', [
            'strategy.plan', 'business.model.canvas', 'swot.analysis',
            'strategy.forecast', 'okr.objective', 'strategy.risk',
        ])
    ])

    # ── 4. Create "Strategy Composer" quest (powerbox) ──
    composer = env['ai.quest'].create({
        'name': 'Strategy Composer',
        'description': (
            'AI-powered business plan generator. Trigger from any strategy '
            'record (strategy.plan, BMC, SWOT, OKRs, risks). The composer '
            'orchestrates specialized agents to generate, analyze, and '
            'synthesize strategy content. Adapts output based on the '
            'target audience (internal, investor, bank, board).'
        ),
        'sub_description': 'Generate business plans, BMCs, SWOTs, and forecasts',
        'init_type': 'powerbox',
        'is_supervisor': True,
        'status': 'active',
        'use_chat_history': True,
        'use_time_context': True,
        'model_ids': [(6, 0, strategy_models.ids)],
    })

    # Assign agents in sequence: strategist → analyst → finance → writer → executor
    quest_agents = [
        (agent_map['agent_strategist'], 1),
        (agent_map['agent_analyst'], 2),
        (agent_map['agent_finance'], 3),
        (agent_map['agent_writer'], 4),
        (agent_map['agent_executor'], 5),
    ]
    for agent, seq in quest_agents:
        env['ai.quest.agent'].create({
            'quest_id': composer.id,
            'agent_id': agent.id,
            'sequence': seq,
        })

    _logger.info('Created quest "%s" with %d agents', composer.name, len(quest_agents))

    # ── 5. Create "Strategy Advisor" quest (chat) ──
    advisor = env['ai.quest'].create({
        'name': 'Strategy Advisor',
        'description': (
            'Free-form strategy consultant. Ask any strategy question — '
            'business model feedback, competitive analysis, growth ideas, '
            'pricing strategy, risk assessment. '
            'Available in the AI chat interface.'
        ),
        'sub_description': 'Your personal strategy consultant — ask anything',
        'init_type': 'chat',
        'status': 'active',
        'show_in_chat': True,
        'use_chat_history': True,
        'use_time_context': True,
        'is_supervisor': False,
    })

    # Advisor uses strategist + executor
    for agent, seq in [
        (agent_map['agent_strategist'], 1),
        (agent_map['agent_executor'], 2),
    ]:
        env['ai.quest.agent'].create({
            'quest_id': advisor.id,
            'agent_id': agent.id,
            'sequence': seq,
        })

    _logger.info('Created chat quest "%s"', advisor.name)

    # ── 6. Create "Strategy Review" quest (manual, cron-ready) ──
    # Cron config can be added later via ir.actions.server + ir.cron.
    review = env['ai.quest'].create({
        'name': 'Strategy Review',
        'description': (
            'Weekly strategy kaizen. Reviews all active strategic plans, '
            'flags risks that have escalated, suggests OKR adjustments, '
            'and identifies stale initiatives. '
            'Configure a Scheduled Action to automate this quest.'
        ),
        'sub_description': 'Weekly automated strategy review and kaizen',
        'init_type': 'manual',
        'status': 'active',
        'is_supervisor': False,
        'use_chat_history': False,
        'use_time_context': True,
    })

    # Review uses executor only (synthesizes everything)
    env['ai.quest.agent'].create({
        'quest_id': review.id,
        'agent_id': agent_map['agent_executor'].id,
        'sequence': 1,
    })

    _logger.info('Created review quest "%s"', review.name)
    # ── 7. Create "Meeting Prep" quest (powerbox) ──
    meeting_model = env['ir.model'].search([('model', '=', 'strategy.meeting')], limit=1)
    model_ids = [(6, 0, [meeting_model.id])] if meeting_model else []

    meeting_prep = env['ai.quest'].create({
        'name': 'Meeting Prep',
        'description': (
            'Prepare a strategic meeting with AI-generated agenda briefs. '
            'Generates content for each agenda item, flags decisions that need '
            'to be made, and summarizes recent strategy changes.'
        ),
        'sub_description': 'AI-powered meeting preparation',
        'init_type': 'powerbox',
        'is_supervisor': False,
        'status': 'active',
        'use_chat_history': False,
        'use_time_context': True,
        'model_ids': model_ids,
    })
    env['ai.quest.agent'].create({
        'quest_id': meeting_prep.id,
        'agent_id': agent_map['agent_board_secretary'].id,
        'sequence': 1,
    })
    _logger.info('Created quest "%s"', meeting_prep.name)

    # ── 8. Create "Meeting Minutes" quest (powerbox) ──
    meeting_minutes = env['ai.quest'].create({
        'name': 'Meeting Minutes',
        'description': (
            'Generate structured meeting minutes from a completed meeting. '
            'Extracts decisions, action items, and risk changes from the agenda.'
        ),
        'sub_description': 'AI-generated meeting minutes',
        'init_type': 'powerbox',
        'is_supervisor': False,
        'status': 'active',
        'use_chat_history': False,
        'use_time_context': True,
        'model_ids': model_ids,
    })
    env['ai.quest.agent'].create({
        'quest_id': meeting_minutes.id,
        'agent_id': agent_map['agent_board_secretary'].id,
        'sequence': 1,
    })
    _logger.info('Created quest "%s"', meeting_minutes.name)

    # ── 9. Create "Meeting Secretary" quest (cron, daily) ──
    meeting_secretary = env['ai.quest'].create({
        'name': 'Meeting Secretary',
        'description': (
            'Daily check of open decisions and governance items. '
            'Flags pending decisions, suggests agenda items for next meeting, '
            'and monitors governance compliance.'
        ),
        'sub_description': 'Daily governance and decision follow-up',
        'init_type': 'manual',  # cron can be added via ir.cron
        'is_supervisor': False,
        'status': 'active',
        'use_chat_history': False,
        'use_time_context': True,
    })
    env['ai.quest.agent'].create({
        'quest_id': meeting_secretary.id,
        'agent_id': agent_map['agent_board_secretary'].id,
        'sequence': 1,
    })
    _logger.info('Created quest "%s"', meeting_secretary.name)

    _logger.info(
        'ai_agent_core_strategy: %d skills, %d agents, %d quests created',
        len(skill_map), len(agent_map), 3)

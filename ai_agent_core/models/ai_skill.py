# -*- coding: utf-8 -*-
"""ai.skill — agentskills.io-compatible skill model."""

import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class AISkill(models.Model):
    _name = 'ai.skill'
    _description = 'AI Skill'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'category, name asc'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    description = fields.Text(required=True,
        help='Max 1024 chars per agentskills.io standard.')

    # Visual
    image_128 = fields.Image('Image', max_width=128, max_height=128)
    color = fields.Integer(default=lambda self: __import__('random').randint(1, 11))

    # Source
    github_url = fields.Char('GitHub URL',
        help='URL to the original skill repository on GitHub')
    source_type = fields.Selection([
        ('odoo', 'Created in Odoo'),
        ('github', 'Imported from GitHub'),
        ('pi', 'Pi Agent Skill'),
    ], default='odoo')

    def _compute_github_avatar(self):
        """Fetch GitHub user avatar when source_type is github."""
        for skill in self:
            if skill.source_type == 'github' and skill.github_url and not skill.image_128:
                import re, base64, urllib.request, ssl
                m = re.search(r'github\.com/([^/]+)', skill.github_url)
                if m:
                    user = m.group(1)
                    try:
                        ctx = ssl.create_default_context()
                        ctx.check_hostname = False
                        ctx.verify_mode = ssl.CERT_NONE
                        req = urllib.request.Request(
                            f'https://github.com/{user}.png',
                            headers={'User-Agent': 'Odoo'}
                        )
                        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                            skill.image_128 = base64.b64encode(resp.read())
                    except Exception:
                        pass

    # Trigger
    trigger_keywords = fields.Char('Trigger Keywords',
        help='Comma-separated keywords')

    # Recipe
    recipe_text = fields.Text('Recipe',
        help='The canonical procedure. Agent reads this when activated.')

    # Compatibility
    compatibility = fields.Selection([
        ('any', 'Any Agent'),
        ('odoo', 'Odoo Agent'),
        ('pi_python', 'Pi Python Agent'),
        ('pi_node', 'Pi Node.js Agent'),
    ], default='any')

    # Required agents
    requires_agent_ids = fields.Many2many('ai.agent', 'ai_skill_required_agent_rel',
        'skill_id', 'agent_id', string='Required Agents',
        help='Agents that must exist for this skill to function. '
             'Auto-created when skill is activated on a coworker.')

    # Verify
    success_cases = fields.Text('Success Cases')
    failure_cases = fields.Text('Failure Cases')

    # Category
    category = fields.Selection([
        ('accounting', 'Accounting'),
        ('development', 'Development'),
        ('infrastructure', 'Infrastructure'),
        ('analysis', 'Analysis'),
        ('communication', 'Communication'),
        ('research', 'Research'),
        ('general', 'General'),
    ], default='general')

    # Stats
    use_count = fields.Integer(default=0)
    version = fields.Integer(default=1)
    last_improved = fields.Datetime('Last Improved')
    agent_count = fields.Integer(compute='_compute_agent_count')
    session_line_count = fields.Integer(compute='_compute_session_line_count')
    session_tokens_last_30d = fields.Integer(
        compute='_compute_session_tokens_30d', string='Tokens (månad)')

    # Improvement
    improvement_guidance = fields.Text('Improvement Guidance')
    improvement_references = fields.Text('Improvement References')
    improvement_suggested_recipe = fields.Text(
        'Föreslaget recept', readonly=True,
        help='Maskingenererat förslag på ny recipe_text, byggt på skillens '
             'egna erfarenheter (success_cases/failure_cases). Granska och '
             'applicera med knappen — inget skrivs över automatiskt.')
    improvement_proposed_on = fields.Datetime(
        'Förslag skapat', readonly=True)

    @api.depends()
    def _compute_agent_count(self):
        for r in self:
            r.agent_count = self.env['ai.agent'].search_count([
                ('skill_ids', 'in', r.id)
            ])

    def _compute_session_line_count(self):
        for r in self:
            r.session_line_count = self.env['ai.coworker.session.line'].search_count(
                [('skill_id', '=', r.id)])

    def _compute_session_tokens_30d(self):
        for r in self:
            r.session_tokens_last_30d = self.env['ai.coworker.session.line']._tokens_this_month(
                extra=[('skill_id', '=', r.id)])

    def action_open_session_lines(self):
        """Open coworker session lines produced by this skill (stat button)."""
        return {
            'name': 'Agenter', 'type': 'ir.actions.act_window',
            'res_model': 'ai.coworker.session.line', 'view_mode': 'list,form',
            'views': [[False, 'list'], [False, 'form']],
            'domain': [('skill_id', '=', self.id)],
        }

    def action_improve(self):
        self.ensure_one()
        self.version += 1
        self.last_improved = fields.Datetime.now()
        self.improvement_guidance = False
        self.improvement_references = False

    def action_apply_kaizen_suggestion(self, suggested_recipe=None, notes=''):
        """Apply a kaizen-approved improvement to this skill (HITL).

        Called after a human approves a kaizen finding. Increments version
        and stores improvement history.

        Args:
            suggested_recipe: optional new recipe_text to apply
            notes: human notes about why the change was approved
        """
        self.ensure_one()
        if suggested_recipe:
            self.recipe_text = suggested_recipe
        self.version += 1
        self.last_improved = fields.Datetime.now()
        if notes:
            self.improvement_references = (
                (self.improvement_references or '') + f'\n[{fields.Datetime.now()}] {notes}'
            ).strip()
        self.improvement_guidance = False
        _logger.info('Skill %s improved to version %d', self.name, self.version)

    def action_use(self):
        self.use_count += 1

    # ── Erfarenheter (steg 2: fånga vad som hände) ───────────────────────

    def record_experience(self, note, verdict='success', source=''):
        """Anteckna en erfarenhet från en körning på denna skill.

        Erfarenheten hamnar i success_cases eller failure_cases — INTE i
        recipe_text. Receptet ändras bara via förbättringsloopen
        (improvement_guidance + action_apply_kaizen_suggestion, HITL), så att
        en enskild körning aldrig tyst skriver om en regel.

        Dedup sker på NOTEN (värd + trigger), inte på källan. 121 larm från
        samma värd+trigger ska bli EN rad med räknare — annars växer fältet
        obegränsat och skickas med i varje prompt (dyrare, inte smartare).
        Källhänvisningen i raden är bara den SENASTE.

        :param note:    vad som hände, en rad ('<värd>: <trigger>')
        :param verdict: 'success' (regeln stämde) | 'failure' (den stämde inte)
        :param source:  spårbarhet, t.ex. 'saltstack.alert 2237'
        :return: True om erfarenheten bokfördes
        """
        import re
        self.ensure_one()
        note = (note or '').strip().replace('\n', ' ')[:500]
        if not note:
            return False
        field_name = 'failure_cases' if verdict == 'failure' else 'success_cases'
        lines = [l for l in (self[field_name] or '').split('\n') if l.strip()]
        today = fields.Date.today().isoformat()

        # Redan känd erfarenhet? Öka räknaren och flytta fram källhänvisningen.
        for i, line in enumerate(lines):
            if note in line:
                m = re.search(r'\(sedd (\d+) ggr', line)
                count = (int(m.group(1)) if m else 1) + 1
                lines[i] = '[%s] %s (sedd %d ggr; senast %s)' % (
                    today, note, count, source or '-')
                self[field_name] = '\n'.join(lines)
                return True

        lines.append('[%s] %s (senast %s)' % (today, note, source or '-'))
        # Tak på antalet rader: fältet skickas med i prompten. Behåll de
        # senaste — äldre erfarenheter har lägre värde än de som är aktuella.
        if len(lines) > self._MAX_EXPERIENCE_LINES:
            lines = lines[-self._MAX_EXPERIENCE_LINES:]
        self[field_name] = '\n'.join(lines)
        _logger.info('Skill %s: erfarenhet (%s) loggad', self.name, verdict)
        return True

    _MAX_EXPERIENCE_LINES = 40

    # ── Steg 4: förbättringsloopen (HITL) ────────────────────────────────

    _MIN_FAILURES_FOR_IMPROVEMENT = 2

    def _experience_lines(self, field_name):
        return [l for l in (self[field_name] or '').split('\n') if l.strip()]

    def propose_improvement(self, use_llm=True, max_iterations=2):
        """Föreslå ny recipe_text utifrån skillens egna erfarenheter.

        Bygger en ImprovementGuidance ur failure_cases (det regeln gjorde fel)
        och success_cases (det den gjorde rätt) och kör den genom
        core/improve.py. Resultatet hamnar i improvement_suggested_recipe —
        **inget skrivs över automatiskt**, människan applicerar.

        Faller tillbaka på ett deterministiskt förslag om LLM-vägen inte
        kan köras (ingen provider, asyncio-problem). Då får man ändå ett
        underlag att bedöma, och loopen är aldrig tyst.

        :return: True om ett förslag skapades
        """
        self.ensure_one()
        failures = self._experience_lines('failure_cases')
        successes = self._experience_lines('success_cases')
        if len(failures) < self._MIN_FAILURES_FOR_IMPROVEMENT:
            return False

        guidance_text = (
            'Regeln i recipe_text aktiveras på ärenden som visat sig vara '
            'falska positiva. Skärp recipe_text så att den inte längre '
            'träffar dessa, utan bara det den faktiskt ska fånga. '
            'Behåll strukturen och språket i receptet.'
        )
        suggested = None
        if use_llm:
            # LLM-vägen får aldrig fälla förslaget: ett kastat fel här
            # (provider nere, timeout, gateway som inte känner modellen)
            # ska ge det deterministiska underlaget i stället. Annars blir
            # loopen tyst — precis det som är farligast, eftersom ingen
            # erfarenhet då någonsin blir ett förslag.
            try:
                suggested = self._llm_improve_recipe(
                    guidance_text, failures, successes, max_iterations)
            except Exception:
                _logger.warning(
                    'LLM-vägen misslyckades för %s — använder '
                    'deterministiskt förslag', self.name, exc_info=True)
                suggested = None
        if not suggested:
            suggested = self._fallback_recipe(failures, successes)

        self.write({
            'improvement_guidance': guidance_text,
            'improvement_references': (
                'Falska positiva (%d):\n%s\n\nLyckade fall (%d):\n%s'
                % (len(failures), '\n'.join(failures[:10]),
                   len(successes), '\n'.join(successes[:5]) or '(inga)')),
            'improvement_suggested_recipe': suggested,
            'improvement_proposed_on': fields.Datetime.now(),
        })
        _logger.info('Skill %s: förbättringsförslag skapat (%d falska positiva)',
                     self.name, len(failures))
        return True

    def _fallback_recipe(self, failures, successes):
        """Deterministiskt förslag när LLM inte kan köras — inget tyst nolläge."""
        base = (self.recipe_text or '').rstrip()
        known = '\n'.join('- ' + f[:180] for f in failures[:10])
        return (
            base
            + '\n\n## Kända falska positiva (inlärda)\n'
            + 'Dessa mönster ska INTE leda till åtgärd:\n'
            + known
        )

    def _improvement_coworker(self):
        """Hitta den medarbetare som äger skillen (för provider-uppslag).

        get_default_provider() kräver en HTTP-request och ger (None, None)
        från cron/shell — därför måste providern hämtas via en coworker, på
        samma sätt som run() gör.
        """
        Coworker = self.env['ai.coworker']
        cw = Coworker.search([('skill_ids', 'in', self.id)], limit=1)
        if cw:
            return cw
        return Coworker.search(
            [('identity_id.skill_ids', 'in', self.id)], limit=1)

    def _improvement_provider(self):
        """(provider, modell) för förbättringsloopen, eller (None, None)."""
        cw = self._improvement_coworker()
        if not cw:
            _logger.info(
                'Ingen coworker äger skillen %s — kan inte hämta provider',
                self.name)
            return None, None
        try:
            from odoo.addons.ai_agent_core.core.provider import ProviderFactory
            return ProviderFactory.from_coworker(cw)
        except Exception:
            _logger.warning('Provider-uppslag misslyckades för %s', self.name,
                            exc_info=True)
            return None, None

    def _llm_improve_recipe(self, guidance_text, failures, successes,
                            max_iterations):
        """Kör core/improve.py synkront. Returnerar nytt recept eller None."""
        import asyncio
        try:
            from odoo.addons.ai_agent_core.core.improve import (
                ImprovementLoop, ImprovementGuidance)

            provider, model_rec = self._improvement_provider()
            if not provider:
                _logger.info('Ingen provider för %s — använder fallback',
                             self.name)
                return None
            model_name = None
            try:
                if model_rec:
                    model_name = (model_rec._get_api_name()
                                  if hasattr(model_rec, '_get_api_name')
                                  else model_rec.name)
            except Exception:
                model_name = None
            if not model_name:
                _logger.info('Ingen modell för %s — använder fallback', self.name)
                return None

            guidance = ImprovementGuidance(
                text=guidance_text,
                false_positives=failures,
                false_negatives=successes[:5],
                severity='medium',
            )
            loop = ImprovementLoop(provider, max_iterations=max_iterations,
                                   model=model_name)

            # Kör i egen tråd med egen event-loop: vi kan stå inuti en
            # körande loop (cron/HTTP) där run_until_complete kastar.
            import threading
            box = {}

            def _worker():
                try:
                    box['run'] = asyncio.new_event_loop().run_until_complete(
                        loop.improve(self.recipe_text or '', guidance))
                except Exception as exc:  # noqa: BLE001
                    box['error'] = exc

            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            t.join(timeout=120)
            if box.get('error') or not box.get('run'):
                _logger.warning('improve.py misslyckades för %s: %s',
                                self.name, box.get('error'))
                return None
            result = box['run'].final_output
            return result if result and result.strip() else None
        except Exception:
            _logger.warning('LLM-förbättring kunde inte köras för %s',
                            self.name, exc_info=True)
            return None

    def action_apply_suggested_recipe(self):
        """HITL: applicera det föreslagna receptet (version++ + stämpel)."""
        self.ensure_one()
        suggested = (self.improvement_suggested_recipe or '').strip()
        if not suggested:
            return False
        self.action_apply_kaizen_suggestion(
            suggested_recipe=suggested,
            notes='Erfarenhetsbaserat förslag godkänt %s'
                  % fields.Datetime.now().strftime('%Y-%m-%d %H:%M'))
        self.improvement_suggested_recipe = False
        self.improvement_proposed_on = False
        return True

    def action_discard_suggested_recipe(self):
        """HITL: kasta förslaget utan att röra receptet."""
        self.ensure_one()
        self.improvement_suggested_recipe = False
        self.improvement_proposed_on = False
        return True

    @api.model
    def _cron_propose_improvements(self, limit=20, commit=True):
        """Cron: skapa förbättringsförslag för skills med nog många erfarenheter.

        Rör aldrig recipe_text — bara improvement_suggested_recipe. Människan
        godkänner i skill-formuläret.

        :param commit: committa per förslag så ett fel inte rullar tillbaka
            hela körningen. Sätts till False i tester (Odoo tillåter inte
            commit inuti en testtransaktion).
        """
        candidates = self.search([
            ('improvement_suggested_recipe', '=', False),
            ('failure_cases', '!=', False),
        ], limit=limit)
        made = 0
        for skill in candidates:
            try:
                if skill.propose_improvement():
                    made += 1
                    if commit:
                        self.env.cr.commit()
            except Exception:
                _logger.warning('Förbättringsförslag misslyckades för %s',
                                skill.name, exc_info=True)
                if commit:
                    self.env.cr.rollback()
        _logger.info('Kaizen: %d förbättringsförslag skapade', made)
        return made

    def action_open_skill_builder(self):
        """Open Skill Builder chat for this skill."""
        self.ensure_one()
        builder = self.env['ai.coworker'].search(
            [('name', '=', 'Skill Builder')], limit=1)
        if not builder:
            return {'type': 'ir.actions.act_url', 'url': '/ai/chat', 'target': 'new'}
        url = f'/ai/chat?coworker_id={builder.id}'
        if self.id:
            url += f'&context_skill={self.id}'
        return {'type': 'ir.actions.act_url', 'url': url, 'target': 'new'}

    def action_export_skill_md(self):
        """Export skill to agentskills.io-compatible SKILL.md."""
        self.ensure_one()
        import re
        n = re.sub(r'[^a-z0-9-]', '', self.name.lower().replace(' ', '-'))[:64]
        d = (self.description or '')[:1024]
        md = f'''---
name: {n}
description: {d}
compatibility: {self.compatibility}
metadata:
  category: {self.category}
  trigger_keywords: {self.trigger_keywords or ''}
---

{self.recipe_text or ''}
'''
        return {
            'type': 'ir.actions.act_window',
            'name': f'Export {self.name}',
            'res_model': 'ai.skill.export.wizard',
            'view_mode': 'form',
            'views': [[False, 'form']],
            'target': 'new',
            'context': {'default_skill_md': md, 'default_skill_id': self.id},
        }


class AISkillExportWizard(models.TransientModel):
    _name = 'ai.skill.export.wizard'
    _description = 'Export Skill to SKILL.md'

    skill_id = fields.Many2one('ai.skill', readonly=True)
    skill_md = fields.Text('SKILL.md', readonly=True)

    def action_copy(self):
        """Copy to clipboard hint."""
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Copied',
                'message': 'SKILL.md content ready — use Ctrl+C to copy',
                'type': 'success',
            }
        }

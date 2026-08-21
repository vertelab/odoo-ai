# -*- coding: utf-8 -*-
"""Skiva 3 — intelligens: regler, intresseprofil, digest, heartbeat.

- Regel-utvärdering (deterministisk pre-filter + LLM-regler i prompten).
- Intresseprofil (hybrid: prompt-text + embedding-likhet, vecko-cron).
- Digest (daglig brief + veckovis djup, per-användare).
- Heartbeat-uppföljning (stale action mail, osedda utkast, förfallna
  follow-ups, Reply Zero).
"""

import asyncio
import json
import logging
import math
from datetime import date, timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

try:
    from ai_agent_core.core.provider import ProviderFactory
except ImportError:  # pragma: no cover
    ProviderFactory = None

_PROFILE_W_LLM_DEFAULT = 0.7


class UserMailAiIntelligence(models.Model):
    _inherit = 'user_mail_ai.mail'

    # ══════════════════════════════════════════════════════════════
    # Regler
    # ══════════════════════════════════════════════════════════════

    def _llm_matched_rules(self):
        try:
            return set(json.loads(self.matched_rules or '[]'))
        except Exception:
            return set()

    def _matching_rules(self):
        """Alla aktiva regler för användaren, sorterade på prioritet."""
        rules = self.env['user_mail_ai.rule'].search([
            ('user_id', '=', self.user_id.id),
            ('active', '=', True),
        ], order='priority')
        return [r for r in rules if self._rule_matches(r)]

    def _rule_matches(self, rule):
        if rule.condition_kind == 'llm':
            return rule.name in self._llm_matched_rules()
        return rule._matches(self)

    def _apply_rules(self):
        """Tillämpa regler — högst prioriterad matchande regel vinner.

        Returns (handled, nudged): handled=True short-circuiter
        default-pipelinen.
        """
        self.ensure_one()
        handled = False
        nudged = False
        for rule in self._matching_rules():
            cfg = rule._parse_config()
            if rule.action == 'ignore':
                self.write({'status': 'ignored',
                            'notes': 'Regel: %s' % rule.name})
                handled = True
            elif rule.action == 'block':
                self.write({'status': 'ignored',
                            'notes': 'Blockerad av regel: %s' % rule.name})
                handled = True
            elif rule.action == 'move_to_folder':
                folder = cfg.get('folder') or 'AI/Arkiv'
                if self._move_to_folder(folder):
                    handled = True
            elif rule.action == 'flag':
                self._flag_action_mail()
            elif rule.action == 'nudge':
                nudged = True
            elif rule.action == 'draft_reply':
                if self._maybe_draft_reply():
                    handled = True
            elif rule.action == 'send_to_specialist':
                specialist = self.env['ai.coworker'].browse(
                    cfg.get('coworker_id')) if cfg.get('coworker_id') \
                    else False
                if self._try_handoff_to(specialist):
                    handled = True
            if handled:
                break
        return handled, nudged

    def _after_classify(self):
        """Hook från base-pipelinen: applicera användarens regler."""
        return self._apply_rules()

    def _move_to_folder(self, folder):
        """Generisk mapp-flytt (AI/Newsletters, AI/Arkiv …)."""
        self.ensure_one()
        if not self.source_uid:
            return False
        imap = self.env['user.mail.imap'].with_user(self.user_id)
        try:
            imap.action_ensure_folder(folder)
            imap.action_move(self.source_folder or 'INBOX',
                             [self.source_uid], folder)
        except Exception as e:
            _logger.warning('Move to %s failed for %s: %s',
                            folder, self.subject, e)
            return False
        self.write({'folder': folder, 'status': 'processed',
                    'notes': 'Flyttad till %s (regel).' % folder})
        return True

    def _try_handoff_to(self, specialist):
        """Handoff till angiven specialist (eller kategori-routing)."""
        self.ensure_one()
        if not specialist:
            return False
        if specialist.status != 'active':
            return False
        try:
            prompt = (
                'Behandla detta mail: kategori=%s, från=%s, ämne=%s.\n\n%s'
                % (self.category or '?', self.from_email or '?',
                   self.subject or '', self._get_body_text(4000)))
            specialist.with_user(self.user_id).run(prompt=prompt)
            self.write({'handoff_coworker_id': specialist.id,
                        'handoff_state': 'handed_off'})
            return True
        except Exception as e:
            _logger.error('Handoff failed for %s: %s', self.subject, e)
            self.write({'handoff_state': 'failed',
                        'notes': 'Handoff misslyckades: %s' % e})
            return False

    # ══════════════════════════════════════════════════════════════
    # Klassificeringskontext: LLM-regler + intresseprofil
    # ══════════════════════════════════════════════════════════════

    def _build_classification_prompt(self):
        prompt = super()._build_classification_prompt()
        user = self.user_id
        llm_rules = self.env['user_mail_ai.rule'].search([
            ('user_id', '=', user.id),
            ('active', '=', True),
            ('condition_kind', '=', 'llm'),
        ])
        if llm_rules:
            rules_txt = '\n'.join('- %s' % r.name for r in llm_rules)
            prompt += (
                '\n\nAnvändarens LLM-regler (utvärdera om mailet matchar):'
                '\n%s\n'
                'Inkludera matchade regelnamn i "matched_rules".'
                % rules_txt)
        if user.ai_profile_text:
            prompt += (
                '\n\nAnvändarens intresseprofil (vad hen bryr sig om):'
                '\n%s' % user.ai_profile_text[:2000])
        # Utöka JSON-schemat med matched_rules
        prompt = prompt.replace(
            '  "reply_suggested": true|false\n}',
            '  "reply_suggested": true|false,\n'
            '  "matched_rules": ["regelnamn", ...]\n}')
        return prompt

    def _classify(self):
        res = super()._classify()
        for rec in self:
            if res and rec.status == 'classified':
                rec._finalize_interest()
        return res

    # ══════════════════════════════════════════════════════════════
    # Intresseprofil (hybrid: prompt + embedding-likhet)
    # ══════════════════════════════════════════════════════════════

    def _provider_and_model(self):
        provider = model = None
        if ProviderFactory is not None:
            try:
                provider, model = ProviderFactory.from_coworker(
                    self._assistant())
            except Exception:
                provider = model = None
        return provider, model

    @api.model
    def _recompute_profiles(self):
        """Vecko-cron: generera om intresseprofilen per användare."""
        users = self.env['res.users'].search([
            ('imap_poll_enabled', '=', True)])
        done = 0
        for user in users:
            try:
                if self._generate_profile(user):
                    done += 1
            except Exception as e:
                _logger.error('Profile generation failed for %s: %s',
                              user.login, e)
        return done

    def _gather_profile_context(self, user):
        """OKF-personligt + company-memory + interaktionshistorik + graf-volym."""
        parts = []
        # 1. OKF personligt scope — senaste koncept
        if 'ai.okf.concept' in self.env:
            try:
                concepts = self.env['ai.okf.concept'].search([
                    ('scope', '=', 'personal'),
                    ('owner_user_id', '=', user.id),
                    ('archived', '=', False),
                ], order='create_date desc', limit=15)
                if concepts:
                    parts.append('Senaste kunskap (OKF):\n' + '\n'.join(
                        '- %s: %s' % (c.title or '?',
                                      (c.summary or '')[:200])
                        for c in concepts))
            except Exception as e:
                _logger.warning('OKF context failed: %s', e)
        # 2. Interaktionshistorik (triage)
        mails = self.search([
            ('user_id', '=', user.id),
            ('status', 'in', ('processed', 'classified')),
        ], order='received_at desc', limit=30)
        if mails:
            parts.append('Mailhistorik:\n' + '\n'.join(
                '- %s (%s, %s)' % (m.from_email or '?',
                                   m.category or '?',
                                   m.subject or '') for m in mails))
        # 3. Graf-volym (mail per partner) — read-only Cypher
        try:
            executor = self.env['graph.executor']
            if executor.is_age_available():
                res = executor.cypher(
                    "MATCH (m:MailMessage)-[:SENT_BY]->(p:OdooPartner) "
                    "RETURN p.name AS partner, count(m) AS vol "
                    "ORDER BY vol DESC LIMIT 8", read_only=True)
                if res:
                    parts.append('Mailvolymer per partner:\n' + '\n'.join(
                        '- %s: %s' % (r.get('partner', '?'),
                                      r.get('vol', 0)) for r in res))
        except Exception as e:
            _logger.warning('Graph profile context failed: %s', e)
        return '\n\n'.join(parts)

    @api.model
    def _generate_profile(self, user):
        """Generera profil-text + profil-embedding för användaren."""
        provider, model = self._provider_and_model()
        if not provider or not model:
            return False
        ctx = self._gather_profile_context(user)
        if not ctx:
            return False
        prompt = (
            'Sammanfatta vad denna användare troligen bryr sig om i sin '
            'mail — som en kort intresseprofil (max 10 rader, på svenska). '
            'Basera på kunskapen nedan. Skriv inte om källorna, bara '
            'slutsatser.\n\n%s' % ctx[:8000])
        try:
            resp = asyncio.run(provider.chat(
                model.api_name or model.name,
                [{'role': 'user', 'content': prompt}],
                temperature=0.3, max_tokens=600))
        except Exception as e:
            _logger.warning('Profile LLM failed for %s: %s', user.login, e)
            return False
        profile_text = (resp.text or '').strip()
        if not profile_text:
            return False
        # Embedding (best-effort) — via ai.provider._get_embedding
        embedding = None
        try:
            provider_rec = self.env['ai.provider'].search(
                [('active', '=', True)], limit=1)
            if provider_rec and hasattr(provider_rec, '_get_embedding'):
                embedding = provider_rec._get_embedding(profile_text)
        except Exception as e:
            _logger.warning('Profile embedding failed for %s: %s',
                            user.login, e)
        user.write({
            'ai_profile_text': profile_text,
            'ai_profile_embedding': json.dumps(embedding) if embedding
            else False,
            'ai_profile_updated': fields.Datetime.now(),
        })
        return True

    def _embedding_similarity(self):
        """Cosinus-likhet mellan mail och profil-embedding (None om saknas)."""
        user = self.user_id
        if not user.ai_profile_embedding:
            return None
        try:
            profile_emb = json.loads(user.ai_profile_embedding)
        except Exception:
            return None
        if not profile_emb:
            return None
        provider_rec = self.env['ai.provider'].search(
            [('active', '=', True)], limit=1)
        if not provider_rec or not hasattr(provider_rec, '_get_embedding'):
            return None
        try:
            mail_emb = provider_rec._get_embedding(
                '%s\n%s' % (self.subject or '', self._get_body_text(2000)))
        except Exception:
            return None
        if not mail_emb:
            return None
        try:
            a = list(profile_emb)
            b = list(mail_emb)
            if len(a) != len(b):
                return None
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(y * y for y in b)) or 1.0
            return dot / (na * nb)
        except Exception:
            return None

    def _finalize_interest(self):
        """Viktad kombination: LLM-intresse + embedding-likhet."""
        self.ensure_one()
        w_llm = self._profile_weight_llm()
        sim = self._embedding_similarity()
        llm_score = self.interest_score or 0.0
        if sim is not None:
            combined = w_llm * llm_score + (1 - w_llm) * sim * 10.0
            self.write({
                'interest_score': round(combined, 1),
                'interest_components': json.dumps({
                    'llm': llm_score,
                    'similarity': round(sim, 3),
                    'weight_llm': w_llm,
                }),
            })

    @api.model
    def _profile_weight_llm(self):
        param = self.env['ir.config_parameter'].sudo().get_param(
            'user_mail_ai.profile_weight_llm', str(_PROFILE_W_LLM_DEFAULT))
        try:
            return float(param)
        except Exception:
            return _PROFILE_W_LLM_DEFAULT

    # ══════════════════════════════════════════════════════════════
    # Digest (daglig brief + veckovis djup)
    # ══════════════════════════════════════════════════════════════

    @api.model
    def _digest_users(self):
        return self.env['res.users'].search([
            ('ai_digest_enabled', '=', True),
            ('imap_poll_enabled', '=', True),
        ])

    @api.model
    def _run_daily_digests(self):
        done = 0
        for user in self._digest_users():
            try:
                if self._build_digest(user, daily=True):
                    done += 1
            except Exception as e:
                _logger.error('Daily digest failed for %s: %s',
                              user.login, e)
        return done

    @api.model
    def _run_weekly_digests(self):
        today = date.today().weekday()
        done = 0
        for user in self._digest_users():
            try:
                if int(user.ai_digest_weekday or 4) == today:
                    if self._build_digest(user, daily=False):
                        done += 1
            except Exception as e:
                _logger.error('Weekly digest failed for %s: %s',
                              user.login, e)
        return done

    @api.model
    def _build_digest(self, user, daily=True):
        provider, model = self._provider_and_model()
        if not provider or not model:
            return False
        since = date.today() - timedelta(days=1 if daily else 7)
        mails = self.search([
            ('user_id', '=', user.id),
            ('received_at', '>=', since.strftime('%Y-%m-%d 00:00:00')),
        ], order='received_at desc', limit=30)
        if not mails:
            return False
        lines = []
        for m in mails:
            lines.append('- %s | %s | %s%s'
                         % (m.from_email or '?', m.category or 'oklassad',
                            m.subject or '',
                            ' [utkast]' if m.draft_uid else ''))
        period = 'igår' if daily else 'senaste veckan'
        prompt = (
            'Sammanfatta %s mail i en kort %s (max %s rader, på svenska): '
            'viktigaste mailet, skapade händelser, öppna utkast och om '
            'något kräver svar. Inga rubriker, bara text.\n\n%s'
            % (period, 'morgonbrief' if daily else 'veckosammanfattning',
               '10' if daily else '25', '\n'.join(lines)))
        try:
            resp = asyncio.run(provider.chat(
                model.api_name or model.name,
                [{'role': 'user', 'content': prompt}],
                temperature=0.4, max_tokens=800))
        except Exception as e:
            _logger.warning('Digest LLM failed for %s: %s', user.login, e)
            return False
        summary = (resp.text or '').strip()
        if not summary:
            return False
        self._notify_user(user, summary)
        self._save_digest_okf(user, summary, daily=daily)
        return True

    def _notify_user(self, user, body):
        """Notis på coworkern (bell) — ingen mail-leverans (undviker loop)."""
        coworker = self._assistant()
        record = coworker if coworker else self.browse()
        if record:
            try:
                record.message_post(
                    body='<p>%s</p>' % body.replace('\n', '<br/>'),
                    message_type='notification',
                    partner_ids=[user.partner_id.id])
                return
            except Exception as e:
                _logger.warning('Digest notify failed: %s', e)
        # Fallback: posta på första triage-posten
        try:
            self.search([('user_id', '=', user.id)], limit=1).sudo().message_post(
                body='<p>%s</p>' % body.replace('\n', '<br/>'),
                message_type='notification',
                partner_ids=[user.partner_id.id])
        except Exception as e:
            _logger.warning('Digest notify fallback failed: %s', e)

    @api.model
    def _save_digest_okf(self, user, summary, daily=True):
        """Spara digesten som OKF-koncept (frågbarhet)."""
        if 'ai.okf.concept' not in self.env:
            return False
        ArtifactType = self.env['ai.artifact.type']
        atype = ArtifactType.search([('name', '=', 'digest')], limit=1) or \
            ArtifactType.search([('name', '=', 'knowledge')], limit=1)
        if not atype:
            atype = ArtifactType.create({'name': 'digest'})
        kind = 'daily' if daily else 'weekly'
        key = 'digest:%s:%s:%s' % (kind, date.today().isoformat(), user.id)
        try:
            self.env['ai.okf.concept']._okf_upsert(
                atype, key, summary, title='Digest %s (%s)'
                % (kind, date.today().isoformat()),
                owner_user_id=user.id, generated_by='process',
                status='stable')
            return True
        except Exception as e:
            _logger.warning('Digest OKF save failed: %s', e)
            return False

    # ══════════════════════════════════════════════════════════════
    # Heartbeat-uppföljning
    # ══════════════════════════════════════════════════════════════

    @api.model
    def _heartbeat_review(self, user=None):
        """Granska öppna mail-ärenden — nudge vid stale action/drafts/
        förfallna follow-ups/Reply Zero. Anropas av coworkern via
        odoo_call_method (skill: granska öppna mail-ärenden).
        """
        users = user or self.env['res.users'].search([
            ('imap_poll_enabled', '=', True)])
        total = 0
        for u in users:
            candidates = self._heartbeat_open_items(u)
            if candidates:
                self._nudge_user(u, candidates)
                for c in candidates:
                    c.write({'follow_up_at': fields.Datetime.now()
                             + timedelta(days=3)})
                total += len(candidates)
        return total

    def _heartbeat_open_items(self, user, stale_days=2, reply_days=5):
        now = fields.Datetime.now()
        items = self.env['user_mail_ai.mail']
        # 1. action_needed som inte nudgats på N dagar
        stale = self.search([
            ('user_id', '=', user.id),
            ('action_needed', '=', True),
            ('status', 'in', ('new', 'classified')),
            ('write_date', '<', (now - timedelta(days=stale_days))
             .strftime('%Y-%m-%d %H:%M:%S')),
        ])
        items |= stale
        # 2. Osedda utkast (äldre än 2 dagar)
        drafts = self.search([
            ('user_id', '=', user.id),
            ('draft_uid', '!=', False),
            ('status', '!=', 'processed'),
            ('write_date', '<', (now - timedelta(days=stale_days))
             .strftime('%Y-%m-%d %H:%M:%S')),
        ])
        items |= drafts
        # 3. Förfallna follow-ups
        due = self.search([
            ('user_id', '=', user.id),
            ('follow_up_at', '!=', False),
            ('follow_up_at', '<', now),
            ('status', '!=', 'processed'),
        ])
        items |= due
        # 4. Reply Zero — kräver svar sedan länge
        rzero = self.search([
            ('user_id', '=', user.id),
            ('reply_needed', '=', True),
            ('status', 'in', ('new', 'classified')),
            ('received_at', '<', (now - timedelta(days=reply_days))
             .strftime('%Y-%m-%d %H:%M:%S')),
        ])
        items |= rzero
        return items

    # ── Seed-hjälp (körs av data-XML <function> vid install + update) ──

    @api.model
    def _ensure_intelligence_skills(self):
        """Koppla heartbeat-skillen till Mail-hjälpredans agent (idempotent)."""
        skill = self.env.ref(
            'user_mail_ai.skill_mail_open_review', raise_if_not_found=False)
        if not skill:
            return False
        coworker = self.env.ref(
            'user_mail_ai.coworker_mail_assistant', raise_if_not_found=False)
        if not coworker:
            return False
        agent = coworker.agent_ids[:1].agent_id if coworker.agent_ids \
            else False
        if agent and skill not in agent.skill_ids:
            agent.write({'skill_ids': [(4, skill.id)]})
        return True

    # ── Default-regler per användare (körs av data-XML <function>) ──

    @api.model
    def _ensure_default_rules(self):
        """Kopiera seed-mallarna till varje användare som aktiverat pollning.

        Idempotent: en användare får varje seed-regel en gång.
        """
        root = self.env.ref('base.user_root')
        templates = self.env['user_mail_ai.rule'].search([
            ('source', '=', 'seed'),
            ('user_id', '=', root.id),
        ])
        if not templates:
            return 0
        users = self.env['res.users'].search([
            ('imap_poll_enabled', '=', True)])
        created = 0
        for user in users:
            existing = set(self.env['user_mail_ai.rule'].search([
                ('user_id', '=', user.id),
                ('source', '=', 'seed'),
            ]).mapped('name'))
            for t in templates:
                if t.name in existing:
                    continue
                self.env['user_mail_ai.rule'].create({
                    'user_id': user.id,
                    'name': t.name,
                    'priority': t.priority,
                    'condition_kind': t.condition_kind,
                    'condition_text': t.condition_text,
                    'action': t.action,
                    'action_config': t.action_config,
                    'source': 'seed',
                })
                created += 1
        return created

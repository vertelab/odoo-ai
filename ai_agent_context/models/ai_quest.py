# -*- coding: utf-8 -*-
##############################################################################
#
#    Copyright (C) 2024- Vertel AB (<https://vertel.se>).
#
##############################################################################

import json
import logging

from odoo import models, fields, api, _

_logger = logging.getLogger(__name__)


class AIQuest(models.Model):
    """Extend AI Quest with automatic record context injection.
    
    Injects three levels of context into the AI system prompt:
    1. User view context — model/view the user is currently in
    2. Record data — JSON of all non-binary fields
    3. Chatter history — message thread (oldest → newest)
    
    Context is resolved from: env.context, session objects, discuss message,
    discuss channel, and the quest's linked channel's ai_context_* fields.
    """
    _inherit = 'ai.quest'

    context_injection_enabled = fields.Boolean(
        string='Enable Record Context',
        default=True,
    )
    context_max_fields = fields.Integer(
        string='Max Context Fields', default=100,
    )
    context_include_chatter = fields.Boolean(
        string='Include Chatter History', default=True,
    )
    context_chatter_limit = fields.Integer(
        string='Chatter Message Limit', default=20,
    )

    # ── _extra_context() — called by build_chain/build_supervisor ──────

    def _extra_context(self):
        """Inject user context + record data + chatter into system prompt."""
        _logger.info("CTX-INJECT [_extra_context] quest=%s enabled=%s",
                     self.name, self.context_injection_enabled)
        res = super()._extra_context()
        if not self.context_injection_enabled:
            _logger.info("CTX-INJECT [_extra_context] DISABLED for quest %s", self.name)
            return res

        parts = []

        # Level 1: User view context from discuss channel
        ch_ctx = self._get_channel_context()
        _logger.info("CTX-INJECT [_extra_context] channel_ctx=%s", ch_ctx)
        if ch_ctx:
            parts.append(
                f"\n\n## User Context\n"
                f"The user is currently viewing: {ch_ctx['model']}"
                + (f" (record ID: {ch_ctx['record_id']})" if ch_ctx.get('record_id') else "")
                + (f" in {ch_ctx['view_type']} view.\n" if ch_ctx.get('view_type') else ".\n")
            )

        # Level 2 & 3: Record fields + chatter
        record = self._get_ai_context_record() or self._get_session_context_record()
        _logger.info("CTX-INJECT [_extra_context] record=%s (name=%s id=%s)",
                     bool(record), record._name if record else '-', record.id if record else '-')
        if record and record.exists():
            try:
                parts.append(
                    f"\n\n## Current Record: {record._name} (ID: {record.id})\n"
                    f"You are interacting within this Odoo record. "
                    f"Use the field data below to answer questions about it.\n"
                )
                try:
                    json_data = record._ai_serialize_fields_data()
                    parts.append(
                        f"### Record Fields\n"
                        f"```json\n{json_data}\n```\n"
                    )
                    _logger.info("CTX-INJECT [_extra_context] fields serialized OK (%d chars)",
                                 len(json_data))
                except Exception as e:
                    _logger.warning("CTX-INJECT [_extra_context] field serialization failed: %s", e)

                if self.context_include_chatter and hasattr(record, '_ai_serialize_messages_data'):
                    try:
                        chatter = record._ai_serialize_messages_data()
                        if chatter:
                            lines = chatter.split('\n')
                            if len(lines) > self.context_chatter_limit:
                                lines = lines[-self.context_chatter_limit:]
                                chatter = '\n'.join(lines) + "\n(older messages omitted)"
                            parts.append(
                                f"### Chatter History (oldest → newest)\n{chatter}\n"
                            )
                            _logger.info("CTX-INJECT [_extra_context] chatter added (%d lines)",
                                         len(lines))
                    except Exception as e:
                        _logger.warning("CTX-INJECT [_extra_context] chatter failed: %s", e)
            except Exception as e:
                _logger.error("CTX-INJECT [_extra_context] failed for %s: %s", self.name, e)
        else:
            _logger.warning("CTX-INJECT [_extra_context] NO record found for quest %s", self.name)

        if parts:
            res += "\n".join(parts)
            _logger.info("CTX-INJECT [_extra_context] DONE — %d parts, %d total chars",
                         len(parts), len(res))
        else:
            _logger.info("CTX-INJECT [_extra_context] NO parts to inject")
        return res

    def _get_channel_context(self):
        """Get user view context from quest's linked discuss channel."""
        channel = self.channel_id or self.real_channel_id
        _logger.info("CTX-INJECT [_get_channel_context] channel_id=%s real_channel_id=%s",
                     self.channel_id, self.real_channel_id)
        if not channel:
            _logger.info("CTX-INJECT [_get_channel_context] no channel linked")
            return None
        model = getattr(channel, 'ai_context_model', False)
        _logger.info("CTX-INJECT [_get_channel_context] channel=%s ai_context_model=%s",
                     channel, model)
        if not model:
            _logger.info("CTX-INJECT [_get_channel_context] no ai_context_model on channel")
            return None
        return {
            'model': model,
            'record_id': getattr(channel, 'ai_context_record_id', False),
            'view_type': getattr(channel, 'ai_context_view_type', False),
        }

    # ── run() — auto-detect & inject record before graph builds ────────

    def run(self, **kwargs):
        """Auto-detect context record and inject before running the quest."""
        _logger.info("CTX-INJECT [run] quest=%s enabled=%s kwargs_keys=%s",
                     self.name, self.context_injection_enabled, list(kwargs.keys()))
        if self.context_injection_enabled:
            record = self._detect_record(kwargs)
            _logger.info("CTX-INJECT [run] detected record=%s",
                         f"{record._name}#{record.id}" if record else "NONE")
            if record and record.exists():
                self = self.with_context(
                    _ai_context_model=record._name,
                    _ai_context_id=record.id,
                )
                kwargs['records'] = record
                _logger.info("CTX-INJECT [run] injected record into kwargs+context")
        return super().run(**kwargs)

    def _get_eval_context(self, action=None, kw=None):
        """Extend eval_context with built record context.
        
        This ensures custom-code Quests (has_code=True) also get context,
        not just non-code Quests that go through build_chain/supervisor.
        """
        ctx = super()._get_eval_context(action=action, kw=kw)
        if self.context_injection_enabled:
            extra = self._extra_context()
            _logger.info("CTX-INJECT [_get_eval_context] extra_context_len=%d has_code=%s",
                         len(extra) if extra else 0, self.has_code)
            if extra:
                ctx['extra_context'] = extra
                ctx['system_context'] = extra
        return ctx

    def _detect_record(self, kwargs):
        """Detect context record from all available sources (priority order)."""
        _logger.info("CTX-INJECT [_detect_record] checking sources...")
        
        # 1. Direct record parameter
        r = kwargs.get('record')
        if r and hasattr(r, 'exists') and r.exists():
            _logger.info("CTX-INJECT [_detect_record] ✅ source1: %s#%s", r._name, r.id)
            return r

        # 2. First from recordset
        records = kwargs.get('records')
        if records and len(records) > 0:
            _logger.info("CTX-INJECT [_detect_record] ✅ source2: %s#%s",
                         records[0]._name, records[0].id)
            return records[0]

        # 3. env.context (systray form button)
        ctx_m = self.env.context.get('context_record_model')
        ctx_id = self.env.context.get('context_record_id')
        if ctx_m and ctx_id:
            try:
                r = self.env[ctx_m].browse(int(ctx_id))
                if r.exists():
                    _logger.info("CTX-INJECT [_detect_record] ✅ source3: %s#%s", ctx_m, ctx_id)
                    return r
            except Exception:
                pass

        # 4. Channel's ai_context_* fields (HTTP request context)
        #    This has HIGHER priority than message model/res_id because
        #    the channel context tells us what record the USER is viewing,
        #    not what record the message happens to be stored on.
        ch = kwargs.get('channel')
        if ch:
            ch_model = getattr(ch, 'ai_context_model', False)
            ch_rid = getattr(ch, 'ai_context_record_id', False)
            if ch_model and ch_rid:
                try:
                    r = self.env[ch_model].browse(int(ch_rid))
                    if r.exists():
                        _logger.info("CTX-INJECT [_detect_record] ✅ source4 channel_ctx: %s#%s",
                                     ch_model, ch_rid)
                        return r
                except Exception:
                    pass

        # 5. Discuss message model/res_id (fallback)
        msg = kwargs.get('message')
        if msg:
            for src in [msg, getattr(msg, 'parent_id', None)]:
                if not src:
                    continue
                m = getattr(src, 'model', None) or getattr(src, 'res_model', None)
                rid = getattr(src, 'res_id', None)
                # Skip discuss.channel — not useful as context
                if m and rid and m != 'discuss.channel':
                    try:
                        r = self.env[m].browse(rid)
                        if r.exists():
                            _logger.info("CTX-INJECT [_detect_record] ✅ source5 message: %s#%s", m, rid)
                            return r
                    except Exception:
                        pass

        # 6. Channel's linked session objects (deep fallback)
        if ch:
            sess = getattr(ch, 'ai_quest_session_id', None)
            if sess and sess.session_object_ids:
                obj = sess.session_object_ids[0]
                if obj.object_id:
                    _logger.info("CTX-INJECT [_detect_record] ✅ source6 session_obj: %s", obj.object_id)
                    return obj.object_id

        _logger.warning("CTX-INJECT [_detect_record] ❌ NO record found from any source!")
        return None

    def _get_ai_context_record(self):
        """Get record from env.context (set by run() override)."""
        m = self.env.context.get('_ai_context_model')
        rid = self.env.context.get('_ai_context_id')
        _logger.info("CTX-INJECT [_get_ai_context_record] env_ctx: _ai_context_model=%s _ai_context_id=%s",
                     m, rid)
        if m and rid:
            try:
                r = self.env[m].browse(int(rid))
                _logger.info("CTX-INJECT [_get_ai_context_record] resolved: %s",
                             f"{r._name}#{r.id} exists={r.exists()}" if r else "NONE")
                return r if r.exists() else None
            except Exception as e:
                _logger.warning("CTX-INJECT [_get_ai_context_record] error: %s", e)
        return None

    def _get_session_context_record(self):
        """Get record from active sessions' objects (fallback)."""
        active = self.session_ids.filtered(lambda x: x.status == 'active')
        _logger.info("CTX-INJECT [_get_session_context_record] active_sessions=%d", len(active))
        for s in active:
            if s.session_object_ids:
                obj = s.session_object_ids[0]
                if obj.object_id:
                    _logger.info("CTX-INJECT [_get_session_context_record] FOUND via session: %s",
                                 obj.object_id)
                    return obj.object_id
        _logger.info("CTX-INJECT [_get_session_context_record] no record in sessions")
        return None

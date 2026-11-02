# -*- coding: utf-8 -*-
from . import _config_param_guard  # noqa: F401 (test-helper)
from . import test_init_types
from . import test_ai_memory_pgvector
from . import test_ai_memory_okf_format
from . import test_init_types_overhaul
# from . import test_provider_resolution  # AVSTÄNGD: kräver `tenacity`, som
# saknas på minionen. tenacity står i /usr/share/odoo-ai/requirements.txt men
# har aldrig installerats — samma rotorsak som de 11 andra saknade paketen.
# Modulen importeras inte heller av core/provider.py i drift: felet syns som
# 99 tysta "No module named 'tenacity'" i Odoo-loggen sedan 23 aug.
# Sätts på igen när beroendet är installerat.
# OKF-mixins kontrakt (F4.1–4.4, D3/D4). Filen fanns men var aldrig
# importerad — samma fälla som test_workspace och test_graph_executor:
# testerna kördes aldrig, vilket är varför det adaptiva taket och den
# garanterade andelen kunde sakna röd test.
from . import test_okf_mixin
from . import test_okf_memory
from . import test_companion_memory
from . import test_okf_fail_closed
from . import test_okf_owner_scoping
# Ägar-åtgärden bakom smartknapparna (okf-smart-buttons F2.2).
from . import test_okf_owner_action
# res.*-källorna når cronen (okf-smart-buttons F4.5). Hoppar sig själv när
# base_ai inte är installerad — bryggan bor i odoo-base.
from . import test_okf_base_ai_cron
from . import test_okf_batched_embedding
from . import test_okf_access_selection
from . import test_core_domain_purity
from . import test_okf_embedding
from . import test_session_summary
from . import test_session_title
from . import test_session_memory_bridge
from . import test_coworker_dispatch_owner
from . import test_memory_consolidation
# Retention är en VERKSTÄLLD frist (memory-retention-enforcement):
# retention_days deklarerades i data men lästes av ingen kod.
from . import test_retention_enforcement
from . import test_okf_dirty_bridge
from . import test_okf_dirty_self_reignite
from . import test_okf_hybrid_search
from . import test_okf_search_no_silent_fallback
from . import test_okf_visible_to
from . import test_lineage
from . import test_odoo_model_tools
from . import test_odoo_model_tools_scenarios
from . import test_tool_access
from . import test_coworker_hitl
from . import test_agent_runtime
from . import test_personal_memory
from . import test_agent_model_resolution
from . import test_external_dispatch
from . import test_hitl_routing
from . import test_partner_enrichment
from . import test_use_cases
from . import test_channel
# Karaktäriseringstester för kanalerna (channel-adapter-wiring 1.1-1.4):
# beteendet FÖRE migreringen till process_item(), oförändrat efter.
from . import test_mail_channel_characterization
from . import test_chat_channel_characterization
from . import test_pwa_push_triggers
from . import test_pi_session_link
# Gateway-sessionsidentiteten (gateway-anropseffektivitet D1): stabilt id
# skilt från visningsnamnet, så korrelationen mot gateway-loggen matchar.
from . import test_gateway_session_identity
# Katalogcache + circuit breaker (gateway-anropseffektivitet D2-D4).
from . import test_gateway_catalog_breaker
# pi-message-persist-dedup: samma Pi-post ger en rad, retry-anrop bevaras.
from . import test_pi_message_dedup
# tools/run coworker-kontext (T/11533): steg 1-prioritering + de
# separerade felmeddelandena (Unknown tool vs not available).
from . import test_tools_run_coworker_context
from . import test_search_settings
from . import test_scheduled_run_failure
from . import test_bifrost_key_resolution
from . import test_openai_api_compaction
# Identitetskopplingar på ai.coworker (coworker-access-identity-sync):
# employee_id / chat_user_id / partner_id samordnas dubbelriktat.
from . import test_coworker_identity_sync
from . import test_session_memory
from . import test_tool_errors
from . import test_write_verify
from . import test_tool_self_correction
from . import test_document_page_acceptance
from . import test_tool_selection
from . import test_round_limit
from . import test_skill_experience
from . import test_daily_budget
from . import test_web_ui_context
from . import test_session_history_relevance
from . import test_actionable_errors
from . import test_ssrf_guard
from . import test_extract
from . import test_content_verification
from . import test_stream_turn_persist
# WebUI:s token-/kostnadsvisning (restore-webui-token-usage 6.1-6.3).
# usage-blocket i thread_get, bokföringen på assistant-raden och
# dubbelbokningsskyddet i _save_client_response.
from . import test_webui_token_usage
# SSE-generatorernas namnbindning (NameError i _cr_holder, 2026-10-11).
# Filen MÅSTE importeras här — annars körs den aldrig. Det är samma fälla
# som test_workspace och test_graph_executor ovan föll i.
from . import test_stream_scope_bindings
from . import test_explicit_agent_tools
from . import test_builtin_fallback_removal
from . import test_record_context
from . import test_agent_identity
# Workspace/PARA + L2/L3 distillation. This file was never imported, which is
# why the _add()/ensure_one() regression in executive_summary.py (nightly
# distillation cron, server action #2848) shipped without a red test.
from . import test_workspace
from . import test_coworker_delegation
# Graf-skrivvägen (odoo-mind-graph-write-path). Filen fanns men var aldrig
# importerad — samma mönster som test_workspace ovan: testerna kördes
# aldrig, vilket är varför en tom graf (0 noder, last_sync=NULL) kunde
# förbli oupptäckt.
from . import test_graph_executor

# Handover (pi-session-handover): tillståndet på sessionen, de tre
# avbrottsformerna, behörighetsgränsen och mode-förslaget.
from . import test_handover

# org-goal-integrity: ägarkedja, onboarding-kaskad, heartbeat målval,
# AI-chefens utförarval och avdelningens ansiktslistor.
from . import test_org_goal_integrity

# schemalagt-arbete (heartbeat-scheduled-work): scheduled_at, dispatchern
# (delad pool, två tak), heartbeat som cron.task, mottagbarhetsbesked och
# behörighetsstyrd aktivering.
from . import test_scheduled_work

# coworker-form-performance: en aggregering i stället för fyra loopar,
# lineage-tabellen och de tre räknarnas semantik.
from . import test_token_totals
from . import test_session_object_lineage
from . import test_session_object_navigation
from . import test_coworker_counters

# coworker-session-smart-button: räknaren mäter månaden, listan visar allt.
# `_session_domain` är EN definition av månaden — delad av räknaren och
# sökvyns `this_month`-filter — så att siffran och listan inte kan glida isär.
from . import test_session_smart_button

# fix-personal-memory-budget-scope D5: stilleståndsvarningen och budget-scopet.
# Filerna fanns men var inte importerade — samma fälla som test_okf_mixin och
# test_workspace: testerna körs aldrig, vilket är varför varningen kunde sakna
# en röd test medan kön stod still i 3 dygn.
from . import test_okf_stall_warning
from . import test_okf_personal_budget_scope

# budget-kanaler (2026-10-27): dagsbudgeten nådde bara 2 av 4 kanaler.
from . import test_budget_channels
# HITL-grinden på /ai/v1/tools/run (ai-agent-core-fault-remediation A3).
# Vägen byggde ToolRegistry och körde tool.execute() utan PermissionEngine —
# de hårda stoppen (odoo_call_method/odoo_unlink) gällde inte där.
from . import test_tools_run_gate
# Strukturtest: varje körväg har samma godkännandegrind
# (init-type-execution-parity). run_stream() exekverade verktyg utan
# PermissionEngine — de hårda stoppen gällde inte på web-UI:ts väg.
from . import test_execution_parity
# Callback-hemlighet + API-nyckelbindning (ai-agent-core-fault-remediation A2/A3).
# Default-hemligheten 'CHANGE_ME_IN_PILLAR' autentiserade vem som helst, och
# vilken giltig API-nyckel som helst kunde läsa vilken session som helst.
from . import test_callback_auth_binding
# Publika /ai/*-rutter: ägarkontroll + åtkomstprövning (A4/A5/A8).
# Rutterna slog upp med sudo() utan att jämföra mot användaren — vem som
# helst kunde läsa/radera/plantera filer i någon annans tråd.
from . import test_public_route_ownership
# A6 (poll-tidsgräns) + B1 (dedupe på tool_call_id).
from . import test_interrupt_poll_and_tool_dedupe
# B2: unik sekvens per session. Migrationen 18.0.1.334 städar dubbletter
# FÖRE constrainten — annars faller ALTER TABLE vid uppgradering.
from . import test_session_line_sequence
# B3: checkpoint/outcome-callbacks i linear, conference och specialist-vägen.
from . import test_loop_callbacks_all_modes
# B4/B6: AutoInterruptHandler fail-closed + select_interrupt_handler kopplad.
from . import test_auto_interrupt_handler_approval
from . import test_hitl_selector_wiring
# B5: HITL utan session — felet ska vara lokaliserbart.
from . import test_hitl_without_session
# B7: provider-livscykel — _run_provider stänger httpx-klienten.
from . import test_provider_lifecycle
# B8: signerad _ai_auto_approve (HMAC bunden till sessionen).
from . import test_auto_approve_signature
# B9/B10: en sekvensstrategi (_next_session_sequence) i hela turen.
from . import test_sequence_strategies
# B11: personal memory — input_type + model, samt backfill.
from . import test_personal_memory_embedding
# B12: ai_memory — död fältgren bort, ingen tyst modellnedladdning.
from . import test_memory_embedding_backend
# fix-memory-vector-dimension: vector(1024) + ivfflat på alla minnesmodeller.
from . import test_memory_vector_dimension
from . import test_pgvector_persistence
# C: hygien — _parse_skill_md med YAML-fortsättning.
from . import test_skill_md_parsing
# wire-orphaned-core-mechanisms 3–6: eval-persistensen (ai.eval.case/run/
# result), befordran session → fall (HITL), eval-cronen och kaizen-fyndet
# när accuracy sjunker. Utan dessa tester är core/eval.py åter oanvänd.
from . import test_eval_pipeline

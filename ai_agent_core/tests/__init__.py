# -*- coding: utf-8 -*-
from . import test_init_types
from . import test_init_types_overhaul
# from . import test_provider_resolution  # AVSTÄNGD: kräver `tenacity`, som
# saknas på minionen. tenacity står i /usr/share/odoo-ai/requirements.txt men
# har aldrig installerats — samma rotorsak som de 11 andra saknade paketen.
# Modulen importeras inte heller av core/provider.py i drift: felet syns som
# 99 tysta "No module named 'tenacity'" i Odoo-loggen sedan 23 aug.
# Sätts på igen när beroendet är installerat.
from . import test_okf_memory
from . import test_okf_embedding
from . import test_session_summary
from . import test_memory_consolidation
from . import test_okf_dirty_bridge
from . import test_okf_hybrid_search
from . import test_lineage
from . import test_odoo_model_tools
from . import test_tool_access
from . import test_coworker_hitl
from . import test_agent_runtime
from . import test_external_dispatch
from . import test_hitl_routing
from . import test_channel
from . import test_pwa_push_triggers
from . import test_pi_session_link
from . import test_search_settings
from . import test_scheduled_run_failure
from . import test_bifrost_key_resolution

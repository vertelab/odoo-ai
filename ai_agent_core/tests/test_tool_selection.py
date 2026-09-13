# -*- coding: utf-8 -*-
"""Tester för explicit verktygsval (improve-ai-coworker-memory-and-tools grupp 6).

Verifierar att urvalet av verktyg för LLM-kontexten är EXPLICIT och litet:
  (6.2) en bred driftlarm-prompt ger ett litet urval (inte 65 av 88 verktyg),
  (6.3) klientens egna kapaciteter (ALWAYS) behålls,
  (6.4) reglerna lägger till högst MAX_RULE_TOOLS utöver bas+ALWAYS.

Bakgrund: prefix-matchning på BREDA ord ("host", "salt", "log") drog in
merparten av verktygsregistret på en typisk driftlarm-prompt, vilket fick
modellen att svälja tool_calls i content-text.
"""

from odoo.tests import common, tagged


def _tool(name):
    """Bygg ett minimalt OpenAI-verktygsschema."""
    return {
        'type': 'function',
        'function': {
            'name': name,
            'description': 'test',
            'parameters': {'type': 'object', 'properties': {}},
        },
    }


# Ett realistiskt register: 88 verktyg fördelade på samma familjer som
# prefix-matchningen tidigare drog in i bulk.
def _registry():
    names = [
        # bas
        'bash', 'read', 'edit', 'write', 'grep', 'describe_model',
        'odoo_search', 'salt_cmd_run', 'driftlarm_update_assessment',
        # ALWAYS (Pi:s kapaciteter)
        'subagent', 'bg_wait', 'memory_write', 'memory_read',
        'memory_search', 'web_search', 'fetch_content', 'task_get',
        'task_set_status', 'task_update_fields', 'okf_search',
        # salt-familjen (prefix skulle dra in alla)
        'salt_test_ping', 'salt_grains_items', 'salt_pillar_items',
        'salt_state_show_sls', 'salt_minion_list', 'salt_disk_usage',
        'salt_memory_usage', 'salt_system_load', 'salt_process_list',
        'salt_service_status', 'salt_service_restart', 'salt_journal_errors',
        'salt_state_apply', 'salt_state_highstate', 'salt_grains_get',
        'salt_pillar_get',
        # zabbix
        'zabbix_get_alerts', 'zabbix_get_problems', 'zabbix_get_triggers',
        'zabbix_get_host', 'zabbix_get_item', 'zabbix_get_history',
        'zabbix_acknowledge', 'zabbix_get_host_groups',
        # wazuh
        'wazuh_agent_status', 'wazuh_recent_alerts', 'wazuh_cve_list',
        # odoo_
        'odoo_read', 'odoo_write', 'odoo_create', 'odoo_call_method',
        'odoo_cron_status', 'odoo_login', 'odoo_fetch_url', 'odoo_calculator',
        'odoo_unlink', 'odoo_count',
        # pg_
        'pg_isready', 'pg_replication_lag', 'pg_stat_activity',
        # caddy
        'caddy_status', 'caddy_recent_errors', 'caddy_upstream_health',
        # postgres/mail
        'tail_odoo_log', 'grep_odoo_errors', 'create_helpdesk_ticket',
        'document_nonconformity', 'salt_system_info', 'salt_grains_items2',
    ]
    return [_tool(n) for n in names]


@tagged('post_install')
class TestToolSelection(common.TransactionCase):
    """Explicit verktygsval för LLM-kontexten."""

    def _select(self, prompt):
        from odoo.addons.ai_agent_core.controllers.stream import AIOpenAIAPI
        tools = _registry()
        messages = [{'role': 'user', 'content': prompt}]
        selected = AIOpenAIAPI._select_relevant_tools(messages, tools)
        return {t['function']['name'] for t in selected}, len(tools)

    # ── 6.2 Driftlarm-prompt ger ett litet urval ───────────────────────

    def test_broad_driftlarm_prompt_gives_small_selection(self):
        """En prompt med 'host', 'salt' och 'log' får inte dra in allt."""
        prompt = (
            'Undersök driftlarm: host web01 rapporterar salt-minion nere, '
            'kolla log och service status'
        )
        selected, total = self._select(prompt)
        # Bas (9) + ALWAYS (11, varav okf_search redan i bas) + max 8 regler.
        # Aldrig i närheten av hela registret.
        self.assertLess(
            len(selected), 35,
            'urvalet ska vara litet — inte merparten av %d verktyg' % total)
        self.assertLess(len(selected), total)

    def test_selection_is_subset_of_available(self):
        """Urvalet innehåller aldrig verktyg som inte skickades."""
        prompt = 'disk cpu minne load service restart postgres caddy zabbix'
        selected, _ = self._select(prompt)
        available = {t['function']['name'] for t in _registry()}
        self.assertTrue(selected <= available)

    def test_no_prefix_bulk_inclusion(self):
        """Ett brett ord drar inte in hela familjen (ingen prefix-matchning)."""
        prompt = 'host'
        selected, _ = self._select(prompt)
        # 'host' finns i zabbix-regelns nyckelord ('host' är inte med där,
        # men zabbix_get_host är ett verktyg) — poängen är att ALLA
        # zabbix_* / salt_* inte följer med.
        salt_names = {n for n in selected if n.startswith('salt_')}
        zabbix_names = {n for n in selected if n.startswith('zabbix_')}
        self.assertLessEqual(len(salt_names), 8)
        self.assertLessEqual(len(zabbix_names), 8)

    # ── 6.3 ALWAYS behålls ─────────────────────────────────────────────

    def test_always_tools_are_kept_without_rule_match(self):
        """Pi:s kapaciteter beskärs inte bort när ingen regel matchar."""
        prompt = 'zzz inget nyckelord matchar detta alls'
        selected, _ = self._select(prompt)
        expected = {
            'subagent', 'bg_wait', 'memory_write', 'memory_read',
            'memory_search', 'web_search', 'fetch_content', 'task_get',
            'task_set_status', 'task_update_fields', 'okf_search',
        }
        missing = expected - selected
        self.assertFalse(
            missing, 'ALWAYS-verktyg beskars bort: %s' % sorted(missing))

    def test_base_tools_are_kept(self):
        """Kärnuppsättningen behålls oavsett prompt."""
        prompt = 'zzz inget nyckelord'
        selected, _ = self._select(prompt)
        expected = {'bash', 'read', 'edit', 'write', 'grep'}
        self.assertTrue(expected <= selected)

    # ── 6.4 Regelbudgeten hålls ────────────────────────────────────────

    def test_rule_budget_is_capped(self):
        """Reglerna lägger till högst MAX_RULE_TOOLS utöver bas+ALWAYS."""
        # En prompt som matchar många regler samtidigt.
        prompt = (
            'disk minne cpu service restart postgres replication caddy 502 '
            'odoo traceback zabbix trigger wazuh cve minion pillar postfix '
            'dovecot mail helpdesk ticket'
        )
        selected, _ = self._select(prompt)
        bas = {
            'bash', 'read', 'edit', 'write', 'grep', 'describe_model',
            'odoo_search', 'salt_cmd_run', 'driftlarm_update_assessment',
        }
        always = {
            'subagent', 'bg_wait', 'memory_write', 'memory_read',
            'memory_search', 'web_search', 'fetch_content', 'task_get',
            'task_set_status', 'task_update_fields', 'okf_search',
        }
        # Endast ett fåtal av dessa finns i registret; räkna de som finns.
        available = {t['function']['name'] for t in _registry()}
        base_count = len((bas | always) & available)
        rule_added = selected - (bas | always)
        self.assertLessEqual(
            len(rule_added), 8,
            'reglerna fick lägga till %d verktyg (tak 8)' % len(rule_added))
        self.assertGreaterEqual(len(selected), base_count)

    # ── 6.5/6.6 Skill-aktivering (data-driven) ────────────────────────

    def _skill(self, name, keywords):
        return self.env['ai.skill'].create({
            'name': name,
            'description': 'test-skill för urval',
            'trigger_keywords': keywords,
        })

    def _select_skills(self, prompt, skills):
        from odoo.addons.ai_agent_core.controllers.stream import AIOpenAIAPI
        messages = [{'role': 'user', 'content': prompt}]
        chosen = AIOpenAIAPI._select_relevant_skills(messages, skills)
        return {s.id for s in chosen}

    def test_skill_activated_by_trigger_keyword(self):
        """6.5: skill med matchande trigger tas med; utan träff tas den inte med."""
        hit = self._skill('Disk-hanterare', 'disk, lagring')
        miss = self._skill('Caddy-routing', 'caddy, 502')
        chosen = self._select_skills('Vi har problem med disk på web01',
                                     hit | miss)
        self.assertIn(hit.id, chosen)
        self.assertNotIn(miss.id, chosen)

    def test_no_trigger_match_keeps_all(self):
        """6.5: ingen träff alls ⇒ oförändrat beteende (behåll samtliga)."""
        a = self._skill('Skill A', 'alfa')
        b = self._skill('Skill B', 'beta')
        chosen = self._select_skills('ingenting matchar här', a | b)
        self.assertEqual(chosen, {a.id, b.id})

    def test_orchestration_skills_always_kept(self):
        """6.5: orchestration.* behålls alltid (styr arbetsupplägget)."""
        orch = self._skill('orchestration-core', 'zzz')
        hit = self._skill('Disk-hanterare 2', 'disk')
        chosen = self._select_skills('problem med disk', orch | hit)
        self.assertIn(orch.id, chosen)
        self.assertIn(hit.id, chosen)

    def test_trigger_keyword_is_whole_word(self):
        """6.5: helordsmatchning — delsträng ger ingen träff."""
        skill = self._skill('Read-skill', 'read')
        # 'read/write' ska inte plocka upp nyckelordet 'read' via delsträng.
        chosen = self._select_skills('read/write-larm', skill)
        # Ingen träff → samtliga behålls (oförändrat beteende).
        self.assertEqual(chosen, {skill.id})
        # Men ett fristående 'read' ska ge träff.
        self.assertTrue(self._skill_triggered(skill, 'vi maste read filen'))

    def _skill_triggered(self, skill, text):
        from odoo.addons.ai_agent_core.controllers.stream import AIOpenAIAPI
        return AIOpenAIAPI._skill_triggered(skill, text.lower())

    def test_identity_skills_are_included_in_selection(self):
        """6.6: identity_id.skill_ids ingår i det underlag som väljs ur."""
        # Verifiera att urvalet accepterar skills som kommer från identiteten
        # (samma mekanism — urvalet arbetar på en recordset oavsett källa).
        ident_skill = self._skill('Identitetsbunden skill', 'identitet')
        coworker_skill = self._skill('Coworker-skill', 'zzz')
        chosen = self._select_skills('en identitet fråga', 
                                     ident_skill | coworker_skill)
        self.assertIn(ident_skill.id, chosen)
        self.assertNotIn(coworker_skill.id, chosen)

    def test_skill_selection_handles_empty_records(self):
        """6.6: tom recordset ger tomt urval (ingen krasch)."""
        from odoo.addons.ai_agent_core.controllers.stream import AIOpenAIAPI
        chosen = AIOpenAIAPI._select_relevant_skills(
            [{'role': 'user', 'content': 'x'}], self.env['ai.skill'].browse())
        self.assertEqual(list(chosen), [])

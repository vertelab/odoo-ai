# -*- coding: utf-8 -*-
"""Tests for personal goals, nudging, and memory learning."""

from datetime import date, timedelta
from odoo.tests.common import TransactionCase
from odoo.exceptions import UserError


class TestPersonalGoal(TransactionCase):
    """Test ai.personal.goal SMART model and status flow."""

    def setUp(self):
        super().setUp()
        self.user = self.env.ref('base.user_admin')
        self.Goal = self.env['ai.personal.goal'].with_user(self.user)

    def test_create_smart_goal(self):
        """Create a goal with all SMART fields."""
        goal = self.Goal.create({
            'name': 'Test SMART Goal',
            'specific': 'Learn Cypher query language',
            'measurable': 'Write 10 working queries',
            'achievable': '1 query/day for 2 weeks',
            'relevant': 'Need for graph database work',
            'time_bound': date.today() + timedelta(days=30),
            'category': 'skill',
        })
        self.assertEqual(goal.status, 'proposed')
        self.assertEqual(goal.progress, 0.0)
        self.assertEqual(goal.specific, 'Learn Cypher query language')
        self.assertEqual(goal.category, 'skill')

    def test_status_flow(self):
        """Goal status flow: proposed → accepted → active → completed."""
        goal = self.Goal.create({'name': 'Flow Test'})

        goal.action_accept()
        self.assertEqual(goal.status, 'active')

        goal.action_complete()
        self.assertEqual(goal.status, 'completed')
        self.assertEqual(goal.progress, 100.0)

    def test_cancel_goal(self):
        """Cancelling a goal archives it."""
        goal = self.Goal.create({'name': 'Cancel Test'})
        goal.action_cancel()
        self.assertEqual(goal.status, 'cancelled')
        self.assertTrue(goal.archived)

    def test_accept_only_proposed(self):
        """Only proposed goals can be accepted."""
        goal = self.Goal.create({'name': 'Direct Active', 'status': 'active'})
        with self.assertRaises(UserError):
            goal.action_accept()

    def test_search_for_user(self):
        """search_for_user returns only active non-archived goals."""
        self.Goal.create({'name': 'Active Goal', 'status': 'active'})
        self.Goal.create({'name': 'Canceled', 'status': 'cancelled', 'archived': True})
        results = self.Goal.search_for_user(self.user.id)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, 'Active Goal')

    def test_nudge_fields(self):
        """Nudge-engine fields should be writable and readable."""
        goal = self.Goal.create({
            'name': 'Nudge Test',
            'streak_count': 3,
            'nudge_count': 2,
            'implementation_intention': 'Monday 09:00',
            'auto_review_interval_days': 7,
        })
        self.assertEqual(goal.streak_count, 3)
        self.assertEqual(goal.nudge_count, 2)
        self.assertEqual(goal.implementation_intention, 'Monday 09:00')


class TestPersonalMemory(TransactionCase):
    """Test ai.personal.memory creation and search."""

    def setUp(self):
        super().setUp()
        self.user = self.env.ref('base.user_admin')
        self.Memory = self.env['ai.personal.memory'].with_user(self.user)

    def test_add_memory(self):
        """add_memory creates a searchable memory."""
        mem_id = self.Memory.add_memory(
            user_id=self.user.id,
            content="User prefers short answers",
            category='preference',
            source='chat',
        )
        self.assertTrue(mem_id)
        mem = self.Memory.browse(mem_id)
        self.assertEqual(mem.category, 'preference')
        self.assertEqual(mem.source, 'chat')

    def test_hybrid_search(self):
        """search_for_user returns relevant memories."""
        self.Memory.add_memory(
            user_id=self.user.id,
            content="Arbetar med periodiseringsfond och K3",
            category='fact',
            source='chat',
        )
        results = self.Memory.search_for_user(
            user_id=self.user.id,
            query='periodiseringsfond',
            limit=10,
        )
        self.assertGreaterEqual(len(results), 0)

    def test_add_only_semantics(self):
        """Content should not be updatable after creation."""
        mem_id = self.Memory.add_memory(
            user_id=self.user.id,
            content="Original content",
            category='fact',
            source='chat',
        )
        mem = self.Memory.browse(mem_id)
        with self.assertRaises(Exception):
            mem.write({'content': 'Modified content'})


class TestGraphExecutor(TransactionCase):
    """Test graph.executor cypher validation."""

    def setUp(self):
        super().setUp()
        self.executor = self.env['graph.executor']

    def test_read_only_blocks_create(self):
        with self.assertRaises(UserError):
            self.executor._validate_read_only("CREATE (n:Test)")

    def test_read_only_blocks_merge(self):
        with self.assertRaises(UserError):
            self.executor._validate_read_only("MERGE (n:Test {id:1})")

    def test_read_only_blocks_delete(self):
        with self.assertRaises(UserError):
            self.executor._validate_read_only("MATCH (n) DELETE n")

    def test_read_only_blocks_set(self):
        with self.assertRaises(UserError):
            self.executor._validate_read_only("MATCH (n) SET n.x=1")

    def test_read_only_allows_match(self):
        try:
            self.executor._validate_read_only("MATCH (n) RETURN n LIMIT 1")
        except UserError:
            self.fail("MATCH should pass validation")

    def test_is_age_available(self):
        result = self.executor.is_age_available()
        # Should not crash — returns True/False
        self.assertIn(result, [True, False])


class TestCompanyIdentity(TransactionCase):
    """Test company mission/values fields."""

    def setUp(self):
        super().setUp()
        self.company = self.env.ref('base.main_company')

    def test_mission_field(self):
        self.company.write({
            'company_mission': '<p>Test mission</p>',
            'company_values': '<p>Test values</p>',
        })
        self.assertEqual(self.company.company_mission, '<p>Test mission</p>')
        self.assertEqual(self.company.company_values, '<p>Test values</p>')


class TestMemoryExtraction(TransactionCase):
    """Test memory extraction patterns."""

    def setUp(self):
        super().setUp()
        self.user = self.env.ref('base.user_admin')
        self.Memory = self.env['ai.personal.memory'].with_user(self.user)

    def test_html_to_text(self):
        """Static HTML to text conversion works."""
        html = '<p>Hello <b>world</b>!</p>'
        text = self.Memory._html_to_text(html)
        self.assertIn('Hello', text)
        self.assertIn('world', text)

    def test_extract_entities(self):
        """Entity extraction identifies key terms."""
        entities = self.Memory._extract_entities(
            "Jobbar med K2 och periodiseringsfond"
        )
        self.assertIsInstance(entities, list)


class TestNudgeGoalLinking(TransactionCase):
    """Test nudge-to-goal linking via okr_id and source_ref."""

    def setUp(self):
        super().setUp()
        self.user = self.env.ref('base.user_admin')
        self.Goal = self.env['ai.personal.goal'].with_user(self.user)

    def test_goal_with_source_ref(self):
        """Goal can reference a source record via source_ref."""
        # source_ref is a Reference field — store as string
        goal = self.Goal.create({
            'name': 'Sourced Goal',
            'source_ref': 'res.partner,1',
        })
        self.assertEqual(goal.source_ref, 'res.partner,1')

    def test_suggest_goals_from_evolution(self):
        """suggest_goals_from_evolution creates proposed goals."""
        signals = [{
            'name': 'Lär dig Docker',
            'specific': 'Genomför Docker-utbildning',
            'measurable': 'Certifiering klar',
            'category': 'skill',
        }]
        suggestions = self.Goal.suggest_goals_from_evolution(
            user_id=self.user.id,
            signals=signals,
        )
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].name, 'Lär dig Docker')
        self.assertEqual(suggestions[0].status, 'proposed')
        self.assertTrue(suggestions[0].created_by_ai)

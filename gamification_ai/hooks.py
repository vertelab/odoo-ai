from odoo import api, SUPERUSER_ID


def post_init_hook(env):
    """Create default badges if they don't exist."""
    Badge = env['gamification.badge']
    existing = Badge.search([('predefined_category', 'ilike', 'ai_%')])
    if existing:
        return

    badge_data = [
        ('First Step', 'Complete your first AI-suggested goal', 'ai_first_goal', 1),
        ('Skill Builder', 'Complete a skill development goal', 'ai_goal_skill', 1),
        ('Knowledge Seeker', 'Complete a knowledge goal', 'ai_goal_knowledge', 1),
        ('Consistency', 'Maintain a 4-week streak', 'ai_streak_4', 1),
        ('Goal Getter', 'Complete 3 goals in one month', 'ai_three_goals', 2),
    ]
    for name, desc, cat, level in badge_data:
        Badge.create({
            'name': name,
            'description': desc,
            'predefined_category': cat,
            'level': level,
            'rule': 'once',
        })

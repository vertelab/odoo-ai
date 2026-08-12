"""Add index on ai.coworker.session.line create_date for burn-rate read_group."""


def migrate(cr, version):
    cr.execute("""
        CREATE INDEX IF NOT EXISTS ai_session_line_create_date_idx
        ON ai_coworker_session_line (create_date)
    """)

"""session-memory-bridge: bron till personligt minne.

Lägger till `memory_extracted` på ai.coworker.session.

Fältet är en spärr, inte en datakälla: det hindrar eftermälet från att
köra samma LLM-extraktion två gånger när det anropas från flera håll
(mark_done, idle-cron, buzz). Befintliga sessioner sätts till False —
ingen har extraherats, eftersom `extract_from_session()` aldrig anropades.

Post-migrate: kolumnen måste finnas innan ORM:en läser fältet.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'ai_coworker_session'
          AND column_name = 'memory_extracted'
    """)
    if cr.fetchone():
        _logger.info('session-memory-bridge: memory_extracted finns redan')
        return

    _logger.info('session-memory-bridge: lägger till memory_extracted')
    cr.execute("""
        ALTER TABLE ai_coworker_session
        ADD COLUMN memory_extracted boolean DEFAULT false
    """)

    cr.execute("""
        UPDATE ai_coworker_session SET memory_extracted = false
        WHERE memory_extracted IS NULL
    """)
    _logger.info(
        'session-memory-bridge: memory_extracted satt på %d sessioner',
        cr.rowcount)

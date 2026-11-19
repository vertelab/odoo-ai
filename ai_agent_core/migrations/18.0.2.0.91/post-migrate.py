# -*- coding: utf-8 -*-
"""Post-migration 18.0.2.0.91: systemtoken-kalibrering (systemtoken-calibration).

TVÅ SAKER, båda om att göra ett tal till en sanning i stället för två.

1. `ai_coworker.total_sys_tokens` blir ett DERIVAT av raderna.
   Fältet var tidigare inkrementellt (`self.total_sys_tokens += ...` per
   körning) medan `session_line_count` räknades ur faktiska rader. Två
   sanningar för samma sak — och den inkrementella kunde dubbelräkna när en
   körning passerade flera skrivvägar (`chat()` och
   `_save_client_response`). Nu räknas det ur `token_sys`, och den här
   migrationen backfillar befintliga värden så de stämmer med raderna.

2. `ai_model.sys_multiplier` räknas om för ALLA modeller.
   Fältet sattes tidigare av en namnheuristik (`_default_sys_multiplier`:
   `deepseek` → 1.0, `claude-3` → 6.0, okänt → 1.0) som inte hade något med
   faktisk kostnad att göra. Nu är det en compute ur modellens pris och
   systemtoken-priset. Migrationen räknar om alla poster — även de som står
   på 6.0 — eftersom hela poängen är att heuristikens värden inte ska
   överleva.

VARFÖR SQL OCH INTE ORM
-----------------------
`ai_coworker_session_line` har 620 000 rader i drift (2,3 GB). Att iterera
dem i Python dödade processen senast (`ai_cost_period._aggregate`,
`psycopg2.DatabaseError: out of memory for query result`, 163 994 rader).
Båda stegen görs därför som enskilda SQL-satser.

⚠ `--init` HOPPAR ÖVER MIGRATIONER
----------------------------------
`checkmodule --init` kör inte migrationsfiler. Därför är allt idempotent, och
`sys_multiplier`-omräkningen är skriven så att den inte kan köra halvvägs:
den sätter alla rader i en sats, inte en loop som kan avbrytas mitt i.

⚠ INGEN `sys_multiplier` PÅ HISTORISKA SESSION LINES
----------------------------------------------------
Rader behåller sin frysta faktor. Att skriva om dem vore att ändra
historisk debitering — kontraktet är att faktorn fryses vid radens
skapande.
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        # Färsk installation: inget att backfilla. Kolumnerna skapas av
        # ORM:en, och compute:n fyller dem.
        return

    _backfill_total_sys_tokens(cr)
    _recalculate_sys_multipliers(cr)


def _backfill_total_sys_tokens(cr):
    """Sätt `total_sys_tokens` = summan av radernas `token_sys`.

    EN sats. `informative = false` är samma filter som `_token_totals` och
    `_compute_total_sys_tokens` använder — informativa rader bär spårbarhet
    (per-agent-tokens från supervisor/konsensus) som redan bokförts på
    sessionen, och att räkna dem skulle dubbelräkna.
    """
    cr.execute("""
        UPDATE ai_coworker c
           SET total_sys_tokens = COALESCE(s.total, 0)
          FROM (
                SELECT coworker_id, SUM(token_sys) AS total
                  FROM ai_coworker_session_line
                 WHERE informative = false
                   AND coworker_id IS NOT NULL
              GROUP BY coworker_id
               ) s
         WHERE c.id = s.coworker_id
    """)
    updated = cr.rowcount

    # Medarbetare utan rader ska vara 0, inte NULL eller ett gammalt
    # inkrementellt värde som aldrig nollställdes.
    cr.execute("""
        UPDATE ai_coworker c
           SET total_sys_tokens = 0
         WHERE NOT EXISTS (
                SELECT 1 FROM ai_coworker_session_line l
                 WHERE l.coworker_id = c.id
                   AND l.informative = false
               )
           AND COALESCE(c.total_sys_tokens, 0) <> 0
    """)
    zeroed = cr.rowcount
    _logger.info(
        'systemtoken-calibration: total_sys_tokens backfillad — %d medarbetare '
        'med rader, %d nollställda', updated, zeroed)


def _recalculate_sys_multipliers(cr):
    """Räkna om `ai_model.sys_multiplier` ur modellens faktiska pris.

    Formeln är densamma som `ai.model._compute_sys_multiplier` och som
    `bifrost.valuation._compute_valuation_data` använder — samma parametrar,
    samma vikter:

        kostnad_per_1M = (cost_input_1k * w_in + cost_output_1k * w_out) * 1000
        sys_multiplier = kostnad_per_1M / systemtoken_pris

    Priset läses ur `ir_config_parameter` (`bifrost.systemtoken_price`,
    fallback `bifrost.systemtoken_price_sek`, default 35). Vikterna ur
    `bifrost.systemtoken_input_weight` (0.25) och
    `bifrost.systemtoken_output_weight` (0.75).

    ⚠ Valuta: `cost_input_1k` är i modellens `pricing_currency_id`
    (vanligen USD) medan systemtoken-priset är i bolagsvalutan. Migrationen
    konverterar INTE — den sätter ett startvärde så ingen post ligger kvar på
    heuristikens tal i mellanläget. Compute:n (`_compute_sys_multiplier`) är
    den auktoritativa vägen: den konverterar via `_convert()` och körs vid
    nästa `--update`/cron.

    ⚠ Det betyder att startvärdet kan vara upp till en växelkurs fel (10x
    vid USD/SEK ~10). Det är avsiktligt: att konvertera i SQL skulle kräva
    att migrationen känner till Odoos kursuppslag, och en felaktig kurs i en
    migration är svårare att upptäcka än ett värde som rättas vid nästa
    cron. `calibration_month` sätts, så avvikelsen syns.

    Modeller utan pris får 0.0 — att gissa ett pris vore värre.
    """
    params = _read_params(cr)
    price = params['price']
    w_in = params['w_in']
    w_out = params['w_out']

    if not price:
        _logger.warning(
            'systemtoken-calibration: systemtoken-priset är 0 — '
            'sys_multiplier kan inte räknas om. Alla modeller lämnas orörda.')
        return

    cr.execute("""
        UPDATE ai_model
           SET sys_multiplier = CASE
                 WHEN COALESCE(cost_input_1k, 0) = 0
                  AND COALESCE(cost_output_1k, 0) = 0
                 THEN 0.0
                 ELSE (
                   (COALESCE(cost_input_1k, 0) * %s
                    + COALESCE(cost_output_1k, 0) * %s) * 1000.0
                 ) / %s
               END
    """, (w_in, w_out, price))
    updated = cr.rowcount

    month = _current_month(cr)
    cr.execute("""
        UPDATE ai_model
           SET calibration_month = %s,
               calibration_price = %s,
               calibration_cost_per_1m =
                 (COALESCE(cost_input_1k, 0) * %s
                  + COALESCE(cost_output_1k, 0) * %s) * 1000.0
    """, (month, price, w_in, w_out))

    cr.execute("""
        SELECT COUNT(*) FROM ai_model
         WHERE COALESCE(cost_input_1k, 0) = 0
           AND COALESCE(cost_output_1k, 0) = 0
    """)
    no_price = cr.fetchone()[0]

    _logger.info(
        'systemtoken-calibration: sys_multiplier omräknad för %d modeller '
        '(%s, pris=%s, w_in=%s, w_out=%s); %d modeller saknar pris och fick 0.0',
        updated, month, price, w_in, w_out, no_price)


def _read_params(cr):
    """Läs systemtoken-priset och vikterna ur ir_config_parameter.

    Samma nycklar och samma defaults som `bifrost.valuation._pricing_params`
    — att läsa dem här i stället för att importera `bifrost` håller
    `ai_agent_core` domän-rent (samma regel som `session-observability`).
    """
    cr.execute("""
        SELECT key, value FROM ir_config_parameter
         WHERE key IN (
            'bifrost.systemtoken_price',
            'bifrost.systemtoken_price_sek',
            'bifrost.systemtoken_input_weight',
            'bifrost.systemtoken_output_weight'
         )
    """)
    raw = {k: v for k, v in cr.fetchall()}

    def _f(key, default):
        try:
            return float(raw.get(key) or default)
        except (TypeError, ValueError):
            return float(default)

    price = _f('bifrost.systemtoken_price',
               _f('bifrost.systemtoken_price_sek', 35))
    return {
        'price': price,
        'w_in': _f('bifrost.systemtoken_input_weight', 0.25),
        'w_out': _f('bifrost.systemtoken_output_weight', 0.75),
    }


def _current_month(cr):
    """Innevarande månad som `YYYY-MM`, räknat i databasens tidszon."""
    cr.execute("SELECT to_char(CURRENT_DATE, 'YYYY-MM')")
    return cr.fetchone()[0]

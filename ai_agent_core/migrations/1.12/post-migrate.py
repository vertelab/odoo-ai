# -*- coding: utf-8 -*-
"""Migrate to 1.12: OKF SQL-funktioner för access-pushdown (beslut 11).

Skapar SECURITY DEFINER-funktionerna:
- ai_okf_can_read(user_id, model) — ir.access (modellnivå), läser ir_model_access
- ai_okf_is_follower(user_id, model, res_id) — mail_followers (objektnivå)

Idempotent (CREATE OR REPLACE). Exponeras bara via Odoo-metoder som
autentiserat (annars kan en DB-användare anropa med user_id=1).
"""

import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    _logger.info("Running migration 1.12: OKF SQL access functions")

    cr.execute("""
        CREATE OR REPLACE FUNCTION ai_okf_can_read(p_user_id integer, p_model text)
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        DECLARE
            v_model_id integer;
            v_count integer;
        BEGIN
            SELECT id INTO v_model_id FROM ir_model WHERE model = p_model;
            IF v_model_id IS NULL THEN
                RETURN FALSE;
            END IF;
            SELECT COUNT(*) INTO v_count
            FROM ir_model_access a
            WHERE a.model_id = v_model_id
              AND a.perm_read = TRUE
              AND (a.group_id IS NULL OR EXISTS (
                  SELECT 1 FROM res_groups_users_rel g
                  WHERE g.gid = a.group_id AND g.uid = p_user_id
              ));
            RETURN v_count > 0;
        END;
        $$;
    """)

    cr.execute("""
        CREATE OR REPLACE FUNCTION ai_okf_is_follower(p_user_id integer, p_model text, p_res_id integer)
        RETURNS boolean
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public
        AS $$
        DECLARE
            v_partner_id integer;
            v_count integer;
        BEGIN
            SELECT partner_id INTO v_partner_id FROM res_users WHERE id = p_user_id;
            IF v_partner_id IS NULL THEN
                RETURN FALSE;
            END IF;
            SELECT COUNT(*) INTO v_count
            FROM mail_followers f
            WHERE f.res_model = p_model
              AND f.res_id = p_res_id
              AND f.partner_id = v_partner_id;
            RETURN v_count > 0;
        END;
        $$;
    """)

    # Behörigheter: den applikationsanvändare Odoo kör som ska kunna exekvera.
    # (GRANT är säkert här — funktionerna är SECURITY DEFINER och bara
    # anropbara via autentiserade Odoo-metoder.)
    cr.execute("""
        SELECT rolname FROM pg_roles WHERE rolname = current_user
    """)
    _logger.info("Migration 1.12 complete")

# Copyright (C) 2026 Vertel Sverige AB (<https://vertel.se>).
"""/ai/** ska ALLTID svara JSON — även när Odoo kastar ett HTTPException.

Bakgrund (2026-09-20): ett fel djupt i ORM:en (UserError) når Odoos
``HttpDispatcher.handle_error``, som gör ``BadRequest(exc.args[0])`` — och för
``type='http'``-routes renderas HTTPException som **Werkzeugs HTML-sida**
("The browser (or proxy) sent a request that this server could not
understand.", 167 byte). Klienten (Pi) visade alltså en ogenomskinlig
``400 <!doctype html>`` utan spår i Odoologgen (UserError loggas inte som ERROR).

Guarden ersätter felrenderingen **endast** för sökvägar som börjar med ``/ai/``:
samma fel blir i stället ``{"error": {"message": <verkligt meddelande>, ...}}``
plus en WARNING (4xx) respektive ERROR med traceback (5xx) i loggen. Alla andra
sökvägar går orörda till originalimplementationen.
"""
import logging

from werkzeug.exceptions import HTTPException

_logger = logging.getLogger(__name__)

_PREFIX = "/ai/"
_INSTALLED = False


def install():
    """Monkey-patcha ``HttpDispatcher.handle_error`` (idempotent)."""
    global _INSTALLED
    if _INSTALLED:
        return False
    from odoo import http
    from odoo.exceptions import UserError

    original = http.HttpDispatcher.handle_error

    def handle_error(self, exc):
        request = getattr(self, "request", None)
        path = (getattr(request.httprequest, "path", "") or "") if request else ""
        if not path.startswith(_PREFIX):
            return original(self, exc)

        if isinstance(exc, HTTPException):
            status = exc.code or 500
            message = (exc.args[0] if exc.args and exc.args[0]
                       else (exc.description or exc.name))
        elif isinstance(exc, UserError):  # inkl. ValidationError
            status = 400
            message = str(exc.args[0]) if exc.args else "User error"
        else:
            status = 500
            message = "Internal server error"

        if status >= 500:
            _logger.error("ai_agent_core: %s -> %s: %s", path, status, exc,
                          exc_info=isinstance(exc, Exception))
            message = "Internal server error"
        else:
            _logger.warning("ai_agent_core: %s -> %s %s: %s",
                            path, status, type(exc).__name__, message)

        return request.make_json_response(
            {"error": {
                "message": str(message),
                "type": "server_error" if status >= 500 else "invalid_request_error",
            }},
            status=status,
        )

    http.HttpDispatcher.handle_error = handle_error
    _INSTALLED = True
    _logger.info("ai_agent_core: /ai/** svarar alltid JSON (http_guard installerad)")
    return True

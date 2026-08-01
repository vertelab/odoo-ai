# -*- coding: utf-8 -*-
"""Webhook controller for ai.coworker — generic HTTP event receiver."""

import json
import logging
import uuid
from odoo import http, fields
from odoo.http import request, Response

_logger = logging.getLogger(__name__)


class AIWebhookController(http.Controller):
    """Webhook endpoint for external systems to trigger AI coworkers."""

    @http.route('/ai/webhook/<int:coworker_id>', type='http', auth='none',
                methods=['POST'], csrf=False, sitemap=False)
    def webhook_receive(self, coworker_id, **kw):
        """Receive an external webhook POST and queue AI processing.

        Auth: Authorization: Bearer <webhook_secret>
        Response: 202 Accepted with event_id

        The coworker processes the event asynchronously via AgentLoop.
        """
        # Validate webhook secret
        auth_header = request.httprequest.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return Response(
                json.dumps({"error": "Missing Authorization header"}),
                status=401, content_type='application/json',
            )

        secret = auth_header[7:]  # Strip 'Bearer '
        coworker = request.env['ai.coworker'].sudo().browse(coworker_id)

        if not coworker.exists():
            return Response(
                json.dumps({"error": "Coworker not found"}),
                status=404, content_type='application/json',
            )

        if not coworker.webhook_secret:
            return Response(
                json.dumps({"error": "Webhook not configured for this coworker"}),
                status=404, content_type='application/json',
            )

        if coworker.webhook_secret != secret:
            return Response(
                json.dumps({"error": "Invalid webhook secret"}),
                status=401, content_type='application/json',
            )

        # Check payload size
        content_length = request.httprequest.content_length or 0
        if content_length > (coworker.max_webhook_payload_size or 1048576):
            return Response(
                json.dumps({"error": "Payload too large"}),
                status=413, content_type='application/json',
            )

        # Parse body
        try:
            payload = json.loads(request.httprequest.data or '{}')
        except json.JSONDecodeError:
            return Response(
                json.dumps({"error": "Invalid JSON payload"}),
                status=400, content_type='application/json',
            )

        # Generate event ID
        event_id = str(uuid.uuid4())

        # Create session for tracking
        session = request.env['ai.coworker.session'].sudo().create({
            'coworker_id': coworker.id,
            'status': 'active',
            'name': f'Webhook: {event_id[:8]}',
            'config_json': json.dumps({
                'event_id': event_id,
                'payload': payload,
            }),
        })

        # Process asynchronously via AgentLoop
        try:
            # Run synchronously for now (async queue can be added later)
            coworker.sudo().run(
                session=session,
                prompt=json.dumps(payload, indent=2),
            )
            session.mark_done('stop')
        except Exception as e:
            _logger.error('Webhook processing error: %s', e, exc_info=True)
            session.write({
                'status': 'error',
                'finish_reason': str(e)[:200],
            })

        return Response(
            json.dumps({
                "event_id": event_id,
                "status": "accepted",
            }),
            status=202, content_type='application/json',
        )

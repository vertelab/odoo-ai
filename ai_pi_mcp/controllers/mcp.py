# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""MCP Streamable HTTP controller.

Implements the *stateful* MCP protocol revision ``2025-03-26`` over HTTP on a
single ``/mcp`` endpoint, matching what the official MCP SDK client (v1.29.x)
negotiates and expects:

* ``POST /mcp``  — JSON-RPC requests (``initialize``, ``notifications/initialized``,
  ``tools/list``, ``tools/call``, ``ping``). Replies are returned as direct
  ``application/json`` (the SDK accepts plain JSON for non-streaming servers).
  A fresh session id is echoed in the ``Mcp-Session-Id`` response header.
* ``GET /mcp``   — the SSE notification stream. Not offered here, so answered
  ``405`` (the SDK treats a 405 on GET as "server offers no SSE" and proceeds).
* ``DELETE /mcp``— terminates the session named in ``Mcp-Session-Id``.

Authentication is Bearer against ``ai_pi_mcp.api_key``. Authenticated calls act
as the developer user configured in ``ai_pi_mcp.developer_user_id`` (expected to
hold ``group_system`` on a development database). The endpoint is dev-only and
guarded by ``ai_pi_mcp.enabled``.
"""

import json
import logging

from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import request

from odoo.addons.ai_pi_mcp.models.mcp_tool import _WRITE_TOOLS

_logger = logging.getLogger(__name__)

# The single protocol revision we serve. Stateful (session-based), which the
# official SDK negotiates by default and the simplest correct revision to serve.
PROTOCOL_VERSION = '2025-03-26'
SERVER_NAME = 'ai_pi_mcp'
SERVER_VERSION = '18.0.1.0.0'

JSONRPC_VERSION = '2.0'

# JSON-RPC error codes (standard).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class McpController(http.Controller):

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def _get_param(self, key, default=None):
        return request.env['ir.config_parameter'].sudo().get_param(key, default)

    def _is_enabled(self):
        return self._get_param('ai_pi_mcp.enabled', 'False') == 'True'

    def _read_only(self):
        return self._get_param('ai_pi_mcp.read_only', 'False') == 'True'

    def _check_auth(self):
        """Validate the Bearer token against the configured API key.

        Returns the developer user (as a sudo env) or raises AccessError.
        """
        auth = request.httprequest.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            raise AccessError('Missing bearer token')
        token = auth[len('Bearer '):].strip()
        expected = self._get_param('ai_pi_mcp.api_key', '')
        if not expected or token != expected:
            raise AccessError('Invalid API key')
        user_id = self._get_param('ai_pi_mcp.developer_user_id')
        if not user_id:
            raise AccessError('ai_pi_mcp.developer_user_id not configured')
        user = request.env['res.users'].sudo().browse(int(user_id))
        if not user.exists():
            raise AccessError('Configured developer user not found')
        return user

    def _run_as(self, user):
        """Return an env acting as ``user`` (sudo) for the current cursor."""
        return request.env(user=user.id)

    # ------------------------------------------------------------------
    # Request body handling
    # ------------------------------------------------------------------

    def _read_jsonrpc(self):
        """Parse the request body into a JSON-RPC message dict or raise."""
        try:
            data = request.get_json_data()
        except Exception:
            data = None
        if not isinstance(data, dict):
            raise ValueError('Request body must be a JSON object')
        return data

    # ------------------------------------------------------------------
    # Response builders
    # ------------------------------------------------------------------

    def _result(self, result, req_id):
        return {'jsonrpc': JSONRPC_VERSION, 'id': req_id, 'result': result}

    def _error(self, code, message, req_id=None, data=None):
        err = {'code': code, 'message': message}
        if data is not None:
            err['data'] = data
        return {'jsonrpc': JSONRPC_VERSION, 'id': req_id, 'error': err}

    def _tool_result(self, text, is_error=False):
        result = {'content': [{'type': 'text', 'text': str(text)}]}
        if is_error:
            result['isError'] = True
        return result

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _handle_initialize(self, params, req_id, env):
        """Create a session and return server info + capabilities."""
        # Negotiate: we serve PROTOCOL_VERSION. The client validates it is in
        # its supported set (it is, for the official SDK).
        session = env['mcp.session'].sudo().create({
            'user_id': env.uid,
            'protocol_version': PROTOCOL_VERSION,
            'initialized': False,
        })
        request._mcp_new_session_id = session.session_id
        return self._result({
            'protocolVersion': PROTOCOL_VERSION,
            'capabilities': {
                'tools': {'listChanged': False},
                'resources': {'subscribe': False, 'listChanged': False},
                'prompts': {'listChanged': False},
                'logging': {},
            },
            'serverInfo': {'name': SERVER_NAME, 'version': SERVER_VERSION},
            'instructions': (
                'ai_pi_mcp developer server for Odoo. Use tools/list to see '
                'available development tools. View edits are backed up '
                'automatically before each change.'
            ),
        }, req_id)

    def _handle_initialized(self, params, req_id, env):
        """Mark the session initialized (fire-and-forget notification)."""
        session_id = request.httprequest.headers.get('Mcp-Session-Id')
        if session_id:
            session = env['mcp.session'].sudo().search([
                ('session_id', '=', session_id),
                ('active', '=', True),
            ], limit=1)
            if session:
                session.write({'initialized': True})
        return None  # notification -> no reply

    def _handle_tools_list(self, params, req_id, env):
        tools = env['mcp.tool'].sudo().get_tools()
        return self._result({'tools': tools}, req_id)

    def _handle_tools_call(self, params, req_id, env):
        name = (params or {}).get('name')
        arguments = (params or {}).get('arguments') or {}
        if not name:
            return self._result(
                self._tool_result('Tool name is required', is_error=True), req_id)
        # Enforce read-only scope on write tools.
        if name in _WRITE_TOOLS and self._read_only():
            return self._result(
                self._tool_result('Server is in read-only mode; tool %s is disabled' % name,
                                  is_error=True),
                req_id)
        try:
            result = env['mcp.tool'].sudo().call(name, arguments, env)
        except (AccessError, UserError) as exc:
            return self._result(self._tool_result(str(exc), is_error=True), req_id)
        except Exception as exc:  # noqa: BLE001
            _logger.exception('ai_pi_mcp tool %s failed', name)
            return self._result(
                self._tool_result('Internal error: %s' % exc, is_error=True), req_id)
        # result may already be a tool result envelope or a plain value.
        if isinstance(result, dict) and 'content' in result:
            return self._result(result, req_id)
        # Serialise structured results as JSON text for the client.
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(result)
        return self._result(self._tool_result(text), req_id)

    def _handle_ping(self, params, req_id, env):
        return self._result({}, req_id)

    def _dispatch(self, data, env):
        """Route one JSON-RPC message to its handler.

        Returns a response dict, or None for notifications (no reply).
        """
        method = data.get('method')
        params = data.get('params') or {}
        req_id = data.get('id')
        if not method:
            return self._error(INVALID_REQUEST, 'Method is required', req_id)

        if method == 'initialize':
            return self._handle_initialize(params, req_id, env)
        if method == 'notifications/initialized':
            return self._handle_initialized(params, req_id, env)
        if method == 'tools/list':
            return self._handle_tools_list(params, req_id, env)
        if method == 'tools/call':
            return self._handle_tools_call(params, req_id, env)
        if method == 'ping':
            return self._handle_ping(params, req_id, env)
        # Resources / prompts / completion are not implemented; advertise empty.
        if method in ('resources/list', 'prompts/list'):
            return self._result({'resources': []} if method == 'resources/list'
                                else {'prompts': []}, req_id)
        if method in ('prompts/get', 'resources/read'):
            return self._result({}, req_id)
        if method == 'completion/complete':
            return self._result({'completion': {'values': [], 'total': 0, 'hasMore': False}}, req_id)
        if method == 'notifications/cancelled':
            return None
        if method.startswith('notifications/'):
            return None
        return self._error(METHOD_NOT_FOUND, 'Method not found: %s' % method, req_id)

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @http.route('/mcp', type='http', auth='none', csrf=False, methods=['POST'],
                save_session=False)
    def mcp_post(self, **kw):
        if not self._is_enabled():
            return request.make_response('ai_pi_mcp is disabled', status=404)
        # Authenticate.
        try:
            user = self._check_auth()
        except AccessError as exc:
            return request.make_response(str(exc), status=401,
                                         headers=[('Content-Type', 'text/plain')])
        env = self._run_as(user)
        # Parse body.
        try:
            data = self._read_jsonrpc()
        except ValueError as exc:
            body = self._error(INVALID_REQUEST, str(exc))
            return request.make_json_response(body, status=400)
        # Validate the session for every method except initialize (which
        # creates the session). A request on a missing/inactive/terminated
        # session must be rejected rather than served.
        method = data.get('method')
        if method != 'initialize':
            session_id = request.httprequest.headers.get('Mcp-Session-Id')
            session = None
            if session_id:
                session = env['mcp.session'].sudo().search([
                    ('session_id', '=', session_id),
                    ('active', '=', True),
                ], limit=1)
            if not session:
                body = self._error(
                    INVALID_REQUEST,
                    'Missing or expired Mcp-Session-Id (call initialize first)',
                    data.get('id'))
                return request.make_json_response(body, status=400)
        # Dispatch.
        try:
            response_data = self._dispatch(data, env)
        except Exception:
            _logger.exception('ai_pi_mcp dispatch error on %s', data.get('method'))
            response_data = self._error(INTERNAL_ERROR, 'Internal server error',
                                        data.get('id'))
        if response_data is None:
            # Notification -> 202 Accepted, no body.
            return request.make_response('', status=202)
        headers = []
        if new_sid := getattr(request, '_mcp_new_session_id', None):
            headers.append(('Mcp-Session-Id', new_sid))
        return request.make_json_response(response_data, headers=headers)

    @http.route('/mcp', type='http', auth='none', csrf=False, methods=['GET'],
                save_session=False)
    def mcp_get(self, **kw):
        # We do not offer an SSE notification stream. The official MCP SDK
        # treats a 405 on GET as "server offers no SSE" and continues normally.
        return request.make_response('SSE not supported', status=405)

    @http.route('/mcp', type='http', auth='none', csrf=False, methods=['DELETE'],
                save_session=False)
    def mcp_delete(self, **kw):
        if not self._is_enabled():
            return request.make_response('ai_pi_mcp is disabled', status=404)
        try:
            user = self._check_auth()
        except AccessError as exc:
            return request.make_response(str(exc), status=401,
                                         headers=[('Content-Type', 'text/plain')])
        env = self._run_as(user)
        session_id = request.httprequest.headers.get('Mcp-Session-Id')
        if session_id:
            session = env['mcp.session'].sudo().search([
                ('session_id', '=', session_id),
                ('active', '=', True),
            ], limit=1)
            if session:
                session.write({'active': False})
        return request.make_response('', status=200)


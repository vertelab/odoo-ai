# -*- coding: utf-8 -*-
"""
Webhook: agenterna skriver sin historik till Odoo.

VARFÖR en webhook och inte direkt databasåtkomst: agenterna kör på
gateways och containrar som inte har Odoo-konfiguration. De har en
NATS-anslutning och en URL. Samma mönster som saltstack.alert —
Bearer-token via hmac.compare_digest, alltid 200.

VARFÖR alltid 200: en agent som får ett fel svarar med retry, och en
retry-loop mot ett trasigt Odoo hjälper ingen. Historiken är viktig men
inte viktigare än att agenten fortsätter arbeta. Specen säger det
uttryckligen: "Odoo är nere men NATS uppe → transporten fungerar ändå;
endast historiken uteblir."
"""

import hmac
import json
import logging

from odoo import fields, http
from odoo.http import request

_logger = logging.getLogger(__name__)


class PiMeshController(http.Controller):

    # ── Hjälpare ───────────────────────────────────────────────────

    def _authorized(self):
        """Kontrollera Bearer-token. True om anropet får skriva."""
        params = request.env['ir.config_parameter'].sudo()
        if params.get_param('pi_mesh.webhook_enabled', 'True') not in (
            'True', 'true', '1',
        ):
            return False
        expected = params.get_param('pi_mesh.webhook_token', '')
        auth = request.httprequest.headers.get('Authorization', '')
        provided = auth[7:] if auth.startswith('Bearer ') else ''
        if not expected or not hmac.compare_digest(provided, expected):
            _logger.warning(
                'pi_mesh-webhook: ogiltig/saknad Bearer token (len=%s)',
                len(provided),
            )
            return False
        return True

    def _payload(self):
        try:
            data = request.get_json_data() or {}
            if isinstance(data, str):
                data = json.loads(data)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            _logger.warning('pi_mesh-webhook: ogiltig JSON: %s', e)
            return {}

    # ── Endpoints ──────────────────────────────────────────────────

    @http.route('/pi_mesh/heartbeat', type='json', auth='none',
                methods=['POST'], csrf=False)
    def heartbeat(self):
        """Ta emot ett pulsslag från en agent.

        Payload:
            agent_id (krävs), hostname, pid, status, task,
            claims[], locks[], skills[]
        """
        if not self._authorized():
            return {'status': 'error', 'error': 'Unauthorized'}
        p = self._payload()
        agent_id = p.get('agent_id')
        if not agent_id:
            return {'status': 'error', 'error': 'agent_id saknas'}

        admin = request.env['res.users'].browse(1)
        agent = request.env['pi.mesh.agent'].sudo().with_user(admin).register_heartbeat(
            agent_id=agent_id,
            hostname=p.get('hostname'),
            pid=p.get('pid'),
            status=p.get('status'),
            task=p.get('task'),
            claims=p.get('claims'),
            locks=p.get('locks'),
            skills=p.get('skills'),
        )
        return {
            'status': 'ok',
            'agent_id': agent_id,
            'agent_db_id': agent.id if agent else None,
            'server_time': str(agent.last_seen) if agent else None,
        }

    @http.route('/pi_mesh/message', type='json', auth='none',
                methods=['POST'], csrf=False)
    def message(self):
        """Logga ett meddelande mellan agenter.

        Payload:
            agent_id (krävs), recipient, kind, subject, body,
            correlation_id, session_ref, task_id
        """
        if not self._authorized():
            return {'status': 'error', 'error': 'Unauthorized'}
        p = self._payload()
        agent_id = p.get('agent_id')
        if not agent_id:
            return {'status': 'error', 'error': 'agent_id saknas'}

        admin = request.env['res.users'].browse(1)
        msg = request.env['pi.mesh.message'].sudo().with_user(admin).create({
            'agent_id': agent_id,
            'recipient': p.get('recipient'),
            'kind': p.get('kind') or 'message',
            'subject': p.get('subject'),
            'body': p.get('body'),
            'response': p.get('response'),
            'correlation_id': p.get('correlation_id'),
            'session_ref': p.get('session_ref'),
            'task_id': p.get('task_id') or False,
            # answered_at sätts när ett svar finns — annars är fältet
            # 'answered' falskt och frågan ser obesvarad ut i vyn.
            'answered_at': fields.Datetime.now() if p.get('response') else False,
        })
        return {'status': 'ok', 'message_id': msg.id}

    @http.route('/pi_mesh/lock', type='json', auth='none',
                methods=['POST'], csrf=False)
    def lock(self):
        """Registrera, förnya eller släpp ett lås.

        Payload:
            action: 'acquire' | 'release'
            resource (krävs), agent_id (krävs), ttl_seconds
        """
        if not self._authorized():
            return {'status': 'error', 'error': 'Unauthorized'}
        p = self._payload()
        action = p.get('action') or 'acquire'
        resource = p.get('resource')
        agent_id = p.get('agent_id')
        if not resource or not agent_id:
            return {'status': 'error', 'error': 'resource och agent_id krävs'}

        admin = request.env['res.users'].browse(1)
        Lock = request.env['pi.mesh.lock'].sudo().with_user(admin)

        if action == 'release':
            n = Lock.release_lock(resource, agent_id)
            return {'status': 'ok', 'released': n}

        lock = Lock.register_lock(
            resource, agent_id, ttl_seconds=p.get('ttl_seconds') or 300,
        )
        return {
            'status': 'ok',
            'lock_id': lock.id,
            'expires_at': str(lock.expires_at),
        }

    @http.route('/pi_mesh/claim', type='json', auth='none',
                methods=['POST'], csrf=False)
    def claim(self):
        """Registrera anspråk på filer (från tool_call-hooken).

        Payload: agent_id (krävs), files[]
        """
        if not self._authorized():
            return {'status': 'error', 'error': 'Unauthorized'}
        p = self._payload()
        agent_id = p.get('agent_id')
        files = p.get('files') or []
        if not agent_id or not files:
            return {'status': 'error', 'error': 'agent_id och files krävs'}

        admin = request.env['res.users'].browse(1)
        Agent = request.env['pi.mesh.agent'].sudo().with_user(admin)
        agent = Agent.search([('name', '=', agent_id)], limit=1)
        if not agent:
            agent = Agent.register_heartbeat(agent_id=agent_id)

        # Anspråk är en helhetsbild, inte en logg: agenten skickar sin
        # fulla lista och vi ersätter. Att lägga till vore att bygga en
        # lista som aldrig krymper.
        agent.write({'claims': '\n'.join(files)})

        # Logga även som meddelande — historiken ska kunna svara på
        # "vem rörde filen?" i efterhand, och anspråksfältet är bara nuet.
        request.env['pi.mesh.message'].sudo().with_user(admin).create({
            'agent_id': agent_id,
            'kind': 'claim',
            'subject': f'{len(files)} fil(er)',
            'body': '\n'.join(files),
        })
        return {'status': 'ok', 'claims': len(files)}

    @http.route('/pi_mesh/task', type='json', auth='none',
                methods=['POST'], csrf=False)
    def task(self):
        """Skapa eller uppdatera en delegerad uppgift.

        Payload:
            action: 'create' | 'update'
            name (krävs vid create), assigned_to, assigned_by,
            description, status, result, task_id (vid update)
        """
        if not self._authorized():
            return {'status': 'error', 'error': 'Unauthorized'}
        p = self._payload()
        action = p.get('action') or 'create'

        admin = request.env['res.users'].browse(1)
        Task = request.env['pi.mesh.task'].sudo().with_user(admin)

        if action == 'update':
            task = Task.browse(int(p.get('task_id') or 0))
            if not task.exists():
                return {'status': 'error', 'error': 'uppgiften finns inte'}
            vals = {}
            for f in ('status', 'result', 'assigned_to'):
                if p.get(f) is not None:
                    vals[f] = p[f]
            if vals.get('status') in ('done', 'failed', 'cancelled'):
                vals['finished_at'] = fields.Datetime.now()
            if vals.get('status') == 'running' and not task.started_at:
                vals['started_at'] = fields.Datetime.now()
            task.write(vals)
            return {'status': 'ok', 'task_id': task.id, 'state': task.status}

        if not p.get('name'):
            return {'status': 'error', 'error': 'name krävs'}
        task = Task.create({
            'name': p['name'],
            'description': p.get('description'),
            'assigned_to': p.get('assigned_to'),
            'assigned_by': p.get('assigned_by'),
            'status': p.get('status') or 'assigned',
            'started_at': fields.Datetime.now()
            if (p.get('status') or 'assigned') == 'running'
            else False,
        })
        # Koppla till Odoos uppgiftsbegrepp (väg c). Görs här och inte i
        # agenten: agenten ska inte behöva känna till Odoos modeller.
        if p.get('link_ai_task', True):
            try:
                task.action_link_ai_task()
            except Exception as e:  # noqa: BLE001
                # Kopplingen är en bonus. Meshen fungerar utan Odoo.
                _logger.warning('pi_mesh: kunde inte koppla ai.org.task: %s', e)
        return {'status': 'ok', 'task_id': task.id}

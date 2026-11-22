# -*- coding: utf-8 -*-
"""ai_agent_zabbix — modulen laddar och Zabbix-klienten beter sig.

VARFÖR DET HÄR TESTET FINNS
===========================
Modulen har en modell (`ai.zabbix.config`) och en HTTP-klient. Klienten
får aldrig anropas i ett test — den går mot en riktig Zabbix-server. Det
som prövas är därför det som går att pröva utan nätverk:

  1. Modulen laddar och modellen finns (annars syns det bara vid install).
  2. `apiinfo.version` skickar INGEN auth-nyckel — Zabbix avvisar anropet
     annars, och felet ser ut som ett trasigt API-token.
  3. `send_event` sväljer fel och returnerar False — en Zabbix som ligger
     nere får inte fälla en cron.
  4. `action_test_connection` sätter `connection_ok` och kastar UserError
     när anropet misslyckas.

`_zabbix_call` mockas i alla tester som når den.

KÖRNING
=======
    odoo-bin -c <conf> -d <db> -u ai_agent_zabbix \\
        --test-tags=/ai_agent_zabbix --stop-after-init
"""

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestZabbixConfig(TransactionCase):
    """ai.zabbix.config — fält, payload-form och felhantering."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Config = cls.env['ai.zabbix.config']
        cls.config = cls.Config.create({
            'name': 'Test Zabbix',
            'url': 'https://zabbix.example/api_jsonrpc.php',
            'api_token': 'test-token',
        })

    def test_01_module_loaded(self):
        """Modulen är installerad och modellen finns."""
        module = self.env['ir.module.module'].search(
            [('name', '=', 'ai_agent_zabbix')], limit=1)
        self.assertTrue(module, 'modulen finns inte i ir.module.module')
        self.assertEqual(module.state, 'installed')
        self.assertIn('ai.zabbix.config', self.env)

    def test_02_defaults(self):
        """Standardvärdena är de som dokumenteras i fältdeklarationen."""
        self.assertTrue(self.config.active)
        self.assertEqual(self.config.timeout, 30)
        self.assertFalse(self.config.verify_ssl)
        self.assertFalse(self.config.connection_ok)

    def test_03_apiinfo_version_sends_no_auth(self):
        """`apiinfo.version` får inte bära auth — Zabbix avvisar det."""
        captured = {}

        def fake_urlopen(req, timeout=None, context=None):
            import json as _json
            captured['payload'] = _json.loads(req.data.decode())

            class _Resp:
                def read(self):
                    return b'{"result": "7.0.0"}'

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False
            return _Resp()

        with patch('urllib.request.urlopen', fake_urlopen):
            result = self.config._zabbix_call('apiinfo.version')

        self.assertEqual(result, '7.0.0')
        self.assertNotIn('auth', captured['payload'],
                         'apiinfo.version skickade auth — Zabbix svarar då fel')
        self.assertEqual(captured['payload']['method'], 'apiinfo.version')

    def test_04_other_methods_send_auth(self):
        """Alla andra metoder bär token i `auth`."""
        captured = {}

        def fake_urlopen(req, timeout=None, context=None):
            import json as _json
            captured['payload'] = _json.loads(req.data.decode())

            class _Resp:
                def read(self):
                    return b'{"result": true}'

                def __enter__(self):
                    return self

                def __exit__(self, *exc):
                    return False
            return _Resp()

        with patch('urllib.request.urlopen', fake_urlopen):
            self.config._zabbix_call('event.create', {'name': 'x'})

        self.assertEqual(captured['payload']['auth'], 'test-token')

    def test_05_send_event_swallows_errors(self):
        """En Zabbix som ligger nere får inte fälla anroparen."""
        with patch.object(type(self.config), '_zabbix_call',
                          side_effect=UserError('nere')):
            self.assertFalse(
                self.config.send_event('test.event'),
                'send_event ska returnera False, inte kasta')

    def test_06_send_event_returns_true_on_success(self):
        with patch.object(type(self.config), '_zabbix_call',
                          return_value={'eventids': ['1']}) as call:
            self.assertTrue(self.config.send_event('test.event', value=1))
        call.assert_called_once()
        self.assertEqual(call.call_args[0][0], 'event.create')

    def test_07_action_test_connection_sets_flag(self):
        """Lyckat test sätter connection_ok och last_test."""
        with patch.object(type(self.config), '_zabbix_call',
                          return_value='7.0.0'):
            action = self.config.action_test_connection()
        self.assertTrue(self.config.connection_ok)
        self.assertTrue(self.config.last_test)
        self.assertEqual(action['tag'], 'display_notification')

    def test_08_action_test_connection_raises_on_failure(self):
        """Misslyckat test kastar UserError — inte en tyst flagga."""
        with patch.object(type(self.config), '_zabbix_call',
                          side_effect=UserError('nere')):
            with self.assertRaises(UserError):
                self.config.action_test_connection()
        self.assertFalse(self.config.connection_ok)

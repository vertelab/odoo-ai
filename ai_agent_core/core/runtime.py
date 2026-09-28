# -*- coding: utf-8 -*-
"""core/runtime.py — dispatch av externa agenter (external-agent-runtime).

Domän-rent: startar/övervakar/dödar en process. Ingen salt/zabbix/caddy-logik.

Varför modulen finns
--------------------
En `ai.coworker` kör sin agent-loop i Odoo-processen och ockuperar därmed en
Odoo-worker under hela körningen — även när den väntar på en LLM eller ett
HITL-godkännande. Med `runtime=external` startas agenten som en separat
process (`pi-agent --mode serve`) och den utlösande workern frigörs direkt.

Odoo äger fortfarande session, LLM-val, budget och verktygsexekvering:
barnprocessen är en tunn klient som talar OpenAI-protokollet mot
`/ai/openai/<coworker_id>/v1/chat/completions`. Ingen ny route införs.

Designreferenser: D1 (runtime är ett fält), D4 (OpenAI-vägen är kanalen),
D5 (användarkontext före spawn), D6 (Odoo pollar, agenten pingar inte),
D9 (runtime-profil), D10 (mätpunkt, inte tröskel).
"""

import errno
import json
import logging
import os
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

_logger = logging.getLogger(__name__)

# ── Konfiguration (ir.config_parameter — sökvägar är inga hemligheter) ──
PARAM_BIN = 'ai_agent_core.pi_agent_bin'
PARAM_PORT_START = 'ai_agent_core.pi_agent_port_start'
PARAM_SPAWN_TIMEOUT = 'ai_agent_core.pi_agent_spawn_timeout'
PARAM_RUN_TIMEOUT = 'ai_agent_core.pi_agent_run_timeout'
PARAM_ABORT_GRACE = 'ai_agent_core.pi_agent_abort_grace'
PARAM_MAX_RSS_MB = 'ai_agent_core.pi_agent_max_rss_mb'

DEFAULT_BIN = '/usr/share/odoo-ai/ai_agent_core/bin/pi-agent'
DEFAULT_PORT_START = 9100
DEFAULT_SPAWN_TIMEOUT = 10.0   # sekunder att vänta på att porten lyssnar
DEFAULT_RUN_TIMEOUT = 300      # sekunder (matchar pi-agents egen default)
DEFAULT_ABORT_GRACE = 5.0      # sekunder mellan SIGTERM och SIGKILL
DEFAULT_MAX_RSS_MB = 50        # D9: bas-RSS vid idle < ~50 MB


def get_param(env, key, default=None):
    """Läs en parameter ur ir.config_parameter (aldrig pillar — ingen hemlighet)."""
    val = env['ir.config_parameter'].sudo().get_param(key, default)
    return val


def get_float(env, key, default):
    raw = get_param(env, key, None)
    if raw in (None, ''):
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        _logger.warning('runtime: parametern %s=%r är inte ett tal — '
                        'använder default %s', key, raw, default)
        return default


def get_int(env, key, default):
    return int(get_float(env, key, default))


def get_bin(env):
    return get_param(env, PARAM_BIN, DEFAULT_BIN) or DEFAULT_BIN


# ── Port ────────────────────────────────────────────────────────────

def find_free_port(start):
    """Första lediga porten från `start`.

    Samma semantik som `pi-agent --find-free-port` (som används av
    dispatchern), men gjord i Odoo så att vi känner porten INNAN vi
    spawnar och kan polla rätt adress.
    """
    for port in range(int(start), int(start) + 200):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(('127.0.0.1', port))
            except OSError as e:
                if e.errno in (errno.EADDRINUSE, errno.EACCES):
                    continue
                raise
            return port
    raise RuntimeError(
        'runtime: ingen ledig port hittades från %s (200 försök).' % start)


# ── API-nyckel (D5: bunden till upplöst användare, aldrig i argv) ────

def write_api_key_file(api_key):
    """Skriv api-nyckeln till en 0600-fil och returnera sökvägen.

    Nyckeln får ALDRIG passera `argv` — argv syns i `ps` för varje
    användare på värden. Filen är 0600 och läses av föräldern; barnet får
    nyckeln via MILJÖVARIABELN `PI_AGENT_API_KEY` (som inte heller syns i
    `ps`), inte via en flagga.

    Filen behålls eftersom livs-heartbeatet (D6) behöver samma nyckel för
    att polla `GET /status` med Bearer-token.
    """
    fd, path = tempfile.mkstemp(prefix='pi-agent-key-', suffix='.txt')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as fh:
            fh.write(api_key)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


# ── Spawn ───────────────────────────────────────────────────────────

class ExternalAgentHandle:
    """Handtag till en startad extern agent-process.

    Bär det Odoo behöver för livscykeln: PID, port, starttid, nyckelfil.
    Ingen Odoo-beroende logik här — bara processen och dess metadata.
    """

    __slots__ = ('proc', 'pid', 'port', 'started_at', 'key_file',
                 'base_url', 'coworker_id', 'name')

    def __init__(self, proc, pid, port, started_at, key_file, base_url,
                 coworker_id, name):
        self.proc = proc
        self.pid = pid
        self.port = port
        self.started_at = started_at
        self.key_file = key_file
        self.base_url = base_url
        self.coworker_id = coworker_id
        self.name = name

    def status_url(self):
        return 'http://127.0.0.1:%d/status' % self.port

    def alive(self):
        """Lever processen? (D6: Odoo observerar, agenten pingar inte.)"""
        if self.proc is not None:
            return self.proc.poll() is None
        return pid_alive(self.pid)

    def rss_kb(self):
        """Processens RSS i kB, eller None om den är borta."""
        return read_rss_kb(self.pid)

    def duration(self):
        return time.time() - self.started_at

    def cleanup_key_file(self):
        if self.key_file:
            try:
                os.unlink(self.key_file)
            except OSError:
                pass
            self.key_file = None


def pid_alive(pid):
    """Finns processen OCH lever den? signal 0 testar existens.

    OBS: `os.kill(pid, 0)` lyckas även för en ZOMBIE (ett barn som dött
    men inte skördats av sin förälder). För en process Odoo själv startat
    är det en verklig skillnad: en zombie är död, men ser levande ut.
    Vi läser därför tillståndet ur /proc och räknar `Z` som död.
    """
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False
        if e.errno == errno.EPERM:
            return True   # finns, men vi får inte signalera den
        return False
    # Finns — men är den en zombie?
    try:
        with open('/proc/%d/stat' % int(pid), 'r') as fh:
            stat = fh.read()
        # Format: pid (comm) state ... — comm kan innehålla parenteser,
        # så vi läser efter SISTA ')'.
        state = stat[stat.rindex(')') + 2]
        if state == 'Z':
            return False
    except (OSError, ValueError, IndexError):
        pass
    return True


def read_rss_kb(pid):
    """Läs VmRSS ur /proc/<pid>/status. None om processen är borta."""
    if not pid:
        return None
    try:
        with open('/proc/%d/status' % int(pid), 'r') as fh:
            for line in fh:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def wait_for_port(port, timeout, proc=None):
    """Vänta tills porten lyssnar (eller processen dör / timeout)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            return False   # dog innan den lyssnade
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(('127.0.0.1', port)) == 0:
                return True
        time.sleep(0.05)
    return False


def spawn(env, coworker_id, api_key, base_url, name=None, skills=None,
          prompt=None, port_start=None, timeout=None):
    """Starta `pi-agent --mode serve` och returnera ett handtag.

    Args:
        env: Odoo-env (för ir.config_parameter).
        coworker_id: int — kopplar processen till /ai/openai/<id>/v1.
        api_key: Bearer-nyckel bunden till den UPPLÖSTA användaren (D5).
        base_url: Odoo-bas-URL som barnprocessen ska tala med.
        name: processnamn (default: pi-agent-<coworker_id>).
        skills: lista av skill-namn att hämta.
        prompt: uppdrag att köra direkt vid start.
        port_start: första port att försöka (default ur parameter).
        timeout: körningens maxsekunder.

    Returns:
        ExternalAgentHandle

    Raises:
        RuntimeError: om binären saknas, porten inte lyssnar, eller
            processen dör innan den är redo. Vi failar högt — en tyst
            halvstartad agent är värre än ingen agent.
    """
    binary = get_bin(env)
    if not os.path.isfile(binary):
        raise RuntimeError(
            'runtime: pi-agent-binären saknas: %s. Sätt %s.'
            % (binary, PARAM_BIN))
    if not os.access(binary, os.X_OK):
        raise RuntimeError('runtime: %s är inte körbar.' % binary)

    if port_start is None:
        port_start = get_int(env, PARAM_PORT_START, DEFAULT_PORT_START)
    if timeout is None:
        timeout = get_int(env, PARAM_RUN_TIMEOUT, DEFAULT_RUN_TIMEOUT)

    port = find_free_port(port_start)
    key_file = write_api_key_file(api_key)
    name = name or ('pi-agent-%s' % coworker_id)

    argv = [
        binary, '--mode', 'serve',
        '--port', str(port),
        '--name', name,
        '--base-url', base_url,
        '--coworker', str(coworker_id),
        '--timeout', str(timeout),
    ]
    if skills:
        argv += ['--skills', ','.join(skills)]
    if prompt:
        argv += ['--prompt', prompt]

    # Nyckeln går via MILJÖN, aldrig via argv: `pi-agent` tar bara
    # `--api-key` eller `PI_AGENT_API_KEY`, och argv syns i `ps`.
    child_env = dict(os.environ)
    child_env['PI_AGENT_API_KEY'] = api_key

    started_at = time.time()
    try:
        proc = subprocess.Popen(
            argv,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,   # egen processgrupp → kan dödas rent
            close_fds=True,
        )
    except Exception:
        try:
            os.unlink(key_file)
        except OSError:
            pass
        raise

    spawn_timeout = get_float(env, PARAM_SPAWN_TIMEOUT, DEFAULT_SPAWN_TIMEOUT)
    if not wait_for_port(port, spawn_timeout, proc=proc):
        rc = proc.poll()
        _terminate(proc, get_float(env, PARAM_ABORT_GRACE, DEFAULT_ABORT_GRACE))
        try:
            os.unlink(key_file)
        except OSError:
            pass
        raise RuntimeError(
            'runtime: pi-agent lyssnade inte på port %d inom %.1f s '
            '(rc=%s).' % (port, spawn_timeout, rc))

    handle = ExternalAgentHandle(
        proc=proc, pid=proc.pid, port=port, started_at=started_at,
        key_file=key_file, base_url=base_url, coworker_id=coworker_id,
        name=name)
    _logger.info(
        'runtime: startade %s (pid=%s, port=%s, coworker=%s) på %.3f s',
        name, proc.pid, port, coworker_id, time.time() - started_at)
    return handle


# ── Livscykel: abort och städning ───────────────────────────────────

def _terminate(proc, grace):
    """SIGTERM → vänta → SIGKILL. Returnerar True om processen är borta."""
    if proc is None:
        return True
    if proc.poll() is not None:
        return True
    try:
        proc.terminate()   # SIGTERM
    except OSError:
        pass
    deadline = time.time() + grace
    while time.time() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    try:
        proc.kill()        # SIGKILL
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
    return proc.poll() is not None


def abort(handle, grace=None, env=None):
    """Avbryt en extern körning: SIGTERM först, SIGKILL vid behov."""
    if grace is None:
        grace = get_float(env, PARAM_ABORT_GRACE, DEFAULT_ABORT_GRACE) \
            if env is not None else DEFAULT_ABORT_GRACE
    if handle is None:
        return True
    gone = _terminate(handle.proc, grace)
    handle.cleanup_key_file()
    _logger.info('runtime: avbröt %s (pid=%s) — borta=%s',
                 handle.name, handle.pid, gone)
    return gone


def kill_pid(pid, grace=None):
    """Döda en process vi bara känner via PID (t.ex. efter Odoo-omstart).

    Returnerar True om processen är borta när vi är klara. Vi skördar
    egna barn (waitpid) så att en zombie inte ser levande ut — en process
    Odoo startat är Odoos barn, och bara Odoo kan skörda den.
    """
    if grace is None:
        grace = DEFAULT_ABORT_GRACE
    if not pid_alive(pid):
        _reap_if_child(pid)
        return True
    try:
        os.kill(int(pid), signal.SIGTERM)
    except OSError:
        _reap_if_child(pid)
        return True
    deadline = time.time() + grace
    while time.time() < deadline:
        if not pid_alive(pid):
            _reap_if_child(pid)
            return True
        time.sleep(0.05)
    try:
        os.kill(int(pid), signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.1)
    _reap_if_child(pid)
    return not pid_alive(pid)


def _reap_if_child(pid):
    """waitpid(WNOHANG) om pid är vårt eget barn — annars inget.

    Utan detta blir ett dödat barn en zombie och `pid_alive` (via
    os.kill) ser det som levande, vilket får städningen att tro att den
    misslyckats.
    """
    try:
        os.waitpid(int(pid), os.WNOHANG)
    except (OSError, ChildProcessError, ValueError):
        pass


# ── Livs-heartbeat (D6: Odoo POLLAR) ────────────────────────────────

def poll_status(handle, timeout=2.0):
    """GET /status med Bearer-nyckeln. Returnerar dict eller None.

    Odoo äger omstart för processer den startat — därför pollar Odoo
    istället för att kräva att agenten pingar.
    """
    if handle is None:
        return None
    api_key = None
    if handle.key_file:
        try:
            with open(handle.key_file, 'r') as fh:
                api_key = fh.read().strip()
        except OSError:
            return None
    req = urllib.request.Request(handle.status_url())
    if api_key:
        req.add_header('Authorization', 'Bearer %s' % api_key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError):
        return None

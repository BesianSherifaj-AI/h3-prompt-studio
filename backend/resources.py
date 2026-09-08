"""Serialize app inference and verify local GPU hand-offs without interrupting jobs."""
from __future__ import annotations
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

import httpx
from .lmstudio import RESIDENT_PREFIX
try:
    import psutil
except ImportError:  # A partial installation still checks HTTP; it never assumes idle.
    psutil = None

class ResourceError(RuntimeError):
    pass

def local_url(url, suffix=''):
    parsed = urlparse(url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use an HTTP endpoint on this computer (127.0.0.1 or localhost).')
    if not parsed.port or not 1 <= parsed.port <= 65535:
        raise ValueError('Include a valid local port in the endpoint.')
    return url.rstrip('/') + suffix


def tcp_listener_ports():
    """Fresh Windows TCP table, or None when absence cannot be established.

    Inspect all local addresses/families conservatively: a listener on the port
    always triggers HTTP verification, including wildcard/dual-stack listeners.
    Some other platforms silently omit inaccessible sockets; do not use their
    negative results to establish that ComfyUI is offline.
    """
    if psutil is None or sys.platform != 'win32':
        return None
    try:
        ports = set()
        for connection in psutil.net_connections(kind='tcp'):
            if connection.status != psutil.CONN_LISTEN:
                continue
            port = connection.laddr.port
            if not isinstance(port, int) or not 1 <= port <= 65535:
                return None
            ports.add(port)
        return frozenset(ports)
    except (psutil.Error, OSError, AttributeError, TypeError, ValueError):
        return None

def gpu_snapshot():
    try:
        run = subprocess.run(['nvidia-smi', '--query-gpu=memory.used,memory.free,memory.total,name', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        values = run.stdout.strip().splitlines()[0].split(',', 3)
        return {'used_mib': int(values[0]), 'free_mib': int(values[1]), 'total_mib': int(values[2]), 'name': values[3].strip()}
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None

class ResourceManager:
    def __init__(self, get_settings, get_client):
        self.get_settings, self.get_client = get_settings, get_client
        self.lock = threading.Lock()
        self.stage = 'idle'
        self.instance_id = None
        self.model_key = None
        self.ai_idle_memory_mib = None
        self.baseline_instance_id = None
        self.last_error = None

    def _forget_instance(self):
        self.instance_id, self.model_key = None, None
        self.ai_idle_memory_mib, self.baseline_instance_id = None, None

    def queues(self):
        queues = []
        listeners = tcp_listener_ports()  # No cache: a newly started server must be checked.
        for url in self.get_settings()['comfy_urls']:
            url = local_url(url)
            port = urlparse(url).port
            if listeners is not None and port not in listeners:
                queues.append({'url': url, 'online': False, 'running': 0, 'pending': 0})
                continue
            try:
                response = httpx.get(url + '/queue', timeout=3, trust_env=False)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data.get('queue_running'), list) or not isinstance(data.get('queue_pending'), list):
                    raise ResourceError(f'Unexpected ComfyUI queue response at {url}.')
                queues.append({'url': url, 'online': True, 'running': len(data['queue_running']), 'pending': len(data['queue_pending'])})
            except httpx.ConnectError as exc:
                # The server may have stopped since the first snapshot. A generic
                # connect error is not proof of closure (nor is a TCP timeout).
                fresh = tcp_listener_ports()
                if fresh is not None and port not in fresh:
                    queues.append({'url': url, 'online': False, 'running': 0, 'pending': 0})
                else:
                    raise ResourceError(f'Cannot confirm ComfyUI is idle at {url}: connection failed and closure is unverified.') from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise ResourceError(f'Cannot confirm ComfyUI is idle at {url}: {type(exc).__name__}.') from exc
        return queues

    def assert_idle(self):
        queues = self.queues()
        busy = [q for q in queues if q['running'] or q['pending']]
        if busy:
            raise ResourceError('ComfyUI has running or queued work. Wait for it to finish before using AI; no job was interrupted.')
        return queues

    def _adopt_previous_resident(self, client, loaded):
        """Recover only an exact, SDK-verified Studio CPU instance after restart."""
        if len(loaded) != 1:
            return False
        item = loaded[0]
        ident = item.get('id', item.get('instance_id'))
        if not isinstance(ident, str) or not ident.startswith(RESIDENT_PREFIX):
            return False
        # Settings may already name the new larger model. Verification must
        # instead resolve this previous instance's own exact inventory key.
        previous_model = item.get('model_key', item.get('model'))
        self.assert_idle()
        verified = client.verify_resident_model(previous_model, ident)
        if (verified.get('instance_id') != ident or verified.get('model') != previous_model
                or verified.get('profile') != 'resident_small_cpu'):
            raise ResourceError('The previous resident model could not be verified; no model was unloaded.')
        self.instance_id, self.model_key = ident, previous_model
        self.ai_idle_memory_mib, self.baseline_instance_id = None, None
        return True

    def _prepare_ai(self, model):
        if self.get_settings().get('ai_memory_mode', 'exclusive') == 'resident_small':
            return self._prepare_resident_ai(model)
        client = self.get_client()
        self.stage = 'checking ComfyUI'
        queues = self.assert_idle()
        online = [q for q in queues if q['online']]
        loaded = client.loaded_instances()
        self._adopt_previous_resident(client, loaded)
        instance_id = lambda item: item.get('id', item.get('instance_id'))
        if self.instance_id and self.model_key != model:
            self.stage = 'unloading previous AI model'
            if any(instance_id(item) == self.instance_id for item in loaded):
                client.unload_model(self.instance_id)
            self._forget_instance()
            loaded = client.loaded_instances()
        matching = [m for m in loaded if m.get('model_key', m.get('model')) == model]
        if any(m not in matching for m in loaded):
            raise ResourceError('Another LM Studio model is loaded. Unload it in LM Studio, then retry with the selected model.')
        if len(matching) > 1:
            raise ResourceError('Multiple instances of the selected LM Studio model are loaded. Keep one instance before preparing AI.')
        owned_baseline = bool(matching and self.model_key == model
                              and instance_id(matching[0]) == self.instance_id == self.baseline_instance_id
                              and self.ai_idle_memory_mib is not None)
        if online and matching and not owned_baseline:
            # An adopted model's total VRAM may include H3. Re-establish ownership
            # once instead of treating unknown global usage as an AI baseline.
            self.stage = 're-establishing the selected AI memory baseline'
            client.unload_model(instance_id(matching[0]))
            self._forget_instance()
            loaded = client.loaded_instances()
            if loaded:
                raise ResourceError('LM Studio still has a model loaded; the selected AI hand-off could not be verified.')
            matching = []
        if online:
            self.stage = 'releasing H3 memory'
            for item in online:
                response = httpx.post(item['url'] + '/free', json={'unload_models': True, 'free_memory': True}, timeout=8, trust_env=False)
                response.raise_for_status()
            # /free is deferred in Comfy's worker. A 200 response is not proof of release.
            deadline = time.monotonic() + 40
            # A resident owned 9B can itself exceed 8 GiB. Its fresh-load baseline
            # is fixed for this instance, never raised by subsequent inferences.
            # Permit 1 GiB for small runtime/display allocation fluctuations.
            release_limit = self.ai_idle_memory_mib + 1024 if owned_baseline else 8192
            time.sleep(1.25)
            while True:
                self.assert_idle()
                memory = gpu_snapshot()
                if memory and memory['used_mib'] < release_limit:
                    break
                if time.monotonic() > deadline:
                    raise ResourceError('H3 memory release could not be verified. Close the H3 model/ComfyUI and retry; no new LM Studio model was loaded.')
                time.sleep(1)
        self.assert_idle()
        self.stage = 'loading vision model'
        loaded = client.loaded_instances()
        matching = [m for m in loaded if m.get('model_key', m.get('model')) == model]
        unrelated = [m for m in loaded if m not in matching]
        if unrelated:
            raise ResourceError('Another LM Studio model is loaded. Unload it in LM Studio, then retry with the selected model.')
        if len(matching) > 1:
            raise ResourceError('Multiple instances of the selected LM Studio model are loaded. Keep one instance before preparing AI.')
        if matching:
            selected_id = instance_id(matching[0])
            if online and (not owned_baseline or selected_id != self.baseline_instance_id):
                raise ResourceError('The loaded AI instance changed during the hand-off. Retry to verify its memory state.')
            if selected_id != self.baseline_instance_id:
                self.ai_idle_memory_mib, self.baseline_instance_id = None, None
            self.instance_id = selected_id
        else:
            self._forget_instance()
            memory = gpu_snapshot()
            if memory and memory['used_mib'] >= 8192:
                raise ResourceError('GPU memory is occupied by another process. Release it before loading the selected AI model; no model was loaded.')
            result = client.load_model(model, context_length=self.get_settings()['context_length'])
            self.instance_id = result['instance_id']
            baseline = gpu_snapshot()
            if baseline is not None and memory is not None:
                self.ai_idle_memory_mib = baseline['used_mib']
                self.baseline_instance_id = self.instance_id
        self.model_key = model
        self.stage = 'AI ready'
        self.last_error = None
        return {'ready': True, 'instance_id': self.instance_id, 'model': model, 'gpu': gpu_snapshot()}

    def _prepare_resident_ai(self, model):
        """Keep H3 untouched while one exact small vision model runs on CPU."""
        settings = self.get_settings()
        if model != settings.get('model'):
            raise ResourceError('Resident mode uses the exact small vision model selected in Connections.')
        self.stage = 'checking resident AI'
        self.assert_idle()
        client = self.get_client()
        client.resident_model_info(model)
        loaded = client.loaded_instances()
        instance_id = lambda item: item.get('id', item.get('instance_id'))
        if self.instance_id and self.model_key != model:
            # Changing the selected model may release only the instance this
            # coordinator already owns. Never adopt/unload unrelated models.
            if any(instance_id(item) != self.instance_id for item in loaded):
                raise ResourceError('Another LM Studio model is loaded. Unload it before preparing the resident small model.')
            if loaded:
                self.stage = 'unloading previous AI model'
                client.unload_model(self.instance_id)
            self._forget_instance()
            loaded = client.loaded_instances()
        if loaded:
            if (len(loaded) != 1 or loaded[0].get('model_key', loaded[0].get('model')) != model):
                raise ResourceError('Resident mode can keep only its selected small vision model. Unload the other LM Studio model first.')
            self.stage = 'verifying resident CPU model'
            result = client.verify_resident_model(model, instance_id(loaded[0]))
        else:
            self._forget_instance()
            self.assert_idle()
            self.stage = 'loading resident CPU vision model'
            result = client.load_resident_model(model)
        self.instance_id, self.model_key = result['instance_id'], model
        self.ai_idle_memory_mib, self.baseline_instance_id = None, None
        self.assert_idle()
        self.stage, self.last_error = 'AI ready · H3 kept loaded', None
        return {**result, 'memory_mode': 'resident_small', 'gpu': gpu_snapshot()}

    def _prepare_resident_h3(self):
        self.stage = 'verifying resident AI before H3'
        self.assert_idle()
        client = self.get_client()
        loaded = client.loaded_instances()
        if loaded:
            model = self.get_settings().get('model')
            client.resident_model_info(model)
            if len(loaded) != 1 or loaded[0].get('model_key', loaded[0].get('model')) != model:
                raise ResourceError('H3 can keep only the verified resident small model. Unload the other LM Studio model first.')
            ident = loaded[0].get('id', loaded[0].get('instance_id'))
            result = client.verify_resident_model(model, ident)
            self.instance_id, self.model_key = result['instance_id'], model
            message = 'The resident CPU vision model stays loaded while H3 renders.'
        else:
            self._forget_instance()
            message = 'No AI model is loaded. H3 is ready.'
        self.stage, self.last_error = 'H3 ready', None
        return {'ready': True, 'memory_mode': 'resident_small', 'gpu': gpu_snapshot(), 'message': message}

    def run_ai(self, model, operation=None):
        if not model:
            raise ResourceError('Select a vision model in Connections first.')
        if not self.lock.acquire(blocking=False):
            raise ResourceError('Another AI or GPU hand-off is in progress. Wait for it to finish.')
        try:
            prepared = self._prepare_ai(model)
            if operation is None:
                return prepared
            self.stage = 'AI is working'
            result = operation(self.instance_id or model)
            self.stage = 'AI ready'
            return result
        except Exception as exc:
            self.last_error = str(exc)
            self.stage = 'needs attention'
            raise
        finally:
            self.lock.release()

    def prepare_h3(self):
        return self.prepare_h3_then()

    def prepare_h3_then(self, operation=None):
        """Keep AI excluded until a caller has finished its one queue submission."""
        if not self.lock.acquire(blocking=False):
            raise ResourceError('AI is still working. H3 has not been queued; wait and retry.')
        try:
            prepared = self._prepare_h3_locked()
            return operation() if operation is not None else prepared
        finally:
            self.lock.release()

    def _prepare_h3_locked(self):
        try:
            if self.get_settings().get('ai_memory_mode', 'exclusive') == 'resident_small':
                return self._prepare_resident_h3()
            self.stage = 'releasing AI memory'
            client = self.get_client()
            loaded = client.loaded_instances()
            released_cpu_resident = self._adopt_previous_resident(client, loaded)
            memory_before = gpu_snapshot()
            released = False
            if self.instance_id:
                if any(m.get('id', m.get('instance_id')) == self.instance_id for m in loaded):
                    client.unload_model(self.instance_id)
                    released = True
            remaining = client.loaded_instances()
            if remaining:
                raise ResourceError('LM Studio still has a model loaded outside this Studio session. Unload it in LM Studio before running H3.')
            self._forget_instance()
            deadline = time.monotonic() + 20
            # CPU placement is verified through the SDK and the native API has
            # confirmed the instance gone. A global 128MiB VRAM drop is not a
            # meaningful requirement for releasing CPU weights and CPU KV.
            while released and not released_cpu_resident:
                memory = gpu_snapshot()
                if not memory or memory['used_mib'] < 4096 or (memory_before and memory_before['used_mib'] - memory['used_mib'] >= 128):
                    break
                # H3 may already be loaded; do not demand it release to prepare itself.
                if any(q['running'] for q in self.queues()):
                    break
                if time.monotonic() > deadline:
                    raise ResourceError('LM Studio reports unloaded, but GPU memory is still occupied. Check the Connections panel before queueing H3.')
                time.sleep(.5)
            self.stage = 'H3 ready'
            self.last_error = None
            return {'ready': True, 'gpu': gpu_snapshot(), 'message': 'LM Studio is unloaded. You can run the H3 workflow.'}
        except httpx.ConnectError:
            # A stopped LM server cannot be verified by its API: never infer model state.
            self.stage = 'needs attention'
            raise ResourceError('Cannot verify LM Studio is unloaded because its server is unavailable. Start its server or disable the Comfy guard after closing LM Studio.')
        except Exception as exc:
            self.last_error = str(exc)
            self.stage = 'needs attention'
            raise

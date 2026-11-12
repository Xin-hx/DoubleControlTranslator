"""Reuse a real Hermes model and prove the application does not unload it."""
import json
from pathlib import Path
import sys
import time
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from hermes_selection_translate.endpoint import resolve_endpoint, resolve_healthy_endpoint, TARGET_SHARED_MODEL
from hermes_selection_translate.manager import WorkerManager
from hermes_selection_translate.model import _opener


def main():
    endpoint = resolve_endpoint()
    base = endpoint['base_url'].rstrip('/').removesuffix('/v1')

    def request(route, body=None):
        headers = {'Content-Type': 'application/json'}
        if endpoint.get('api_key'):
            headers['Authorization'] = 'Bearer ' + endpoint['api_key']
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + route, headers=headers, data=data)
        with _opener().open(req, timeout=60) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}

    def model_status():
        entries = request('/models')['data']
        item = next(m for m in entries if m['id'] == TARGET_SHARED_MODEL)
        status = item.get('status', '')
        return status.get('value') if isinstance(status, dict) else status

    original_state = model_status()
    loaded_for_test = original_state not in {'loaded', 'running'}
    manager = WorkerManager()
    result = {}
    try:
        if loaded_for_test:
            request('/models/load', {'model': TARGET_SHARED_MODEL})
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and model_status() not in {'loaded', 'running'}:
                time.sleep(.25)
        shared = resolve_healthy_endpoint()
        exe = ROOT / 'dist/HermesTool/HermesTool.exe'
        def command(mode, *args):
            assert mode == 'worker', 'shared reuse tried to spawn an owned model'
            return [str(exe), '--worker', *args, '--allow-test-marker']
        with patch('hermes_selection_translate.manager.child_command', side_effect=command):
            state = manager.enable_and_load()
            assert state['state'] == 'running' and state['model'] == 'shared', state
            assert not manager.model.running()
            assert manager.test_card()['accepted']
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                manager._send({'op': 'status'})
                if manager.diagnostics.get('test_result'):
                    break
                time.sleep(.1)
            assert any('\u4e00' <= c <= '\u9fff' for c in manager.diagnostics.get('test_result', ''))
            manager.disable()
            assert resolve_healthy_endpoint()['base_url'] == shared['base_url']
            assert model_status() in {'loaded', 'running'}
            result = {'real_shared_model_reused': True, 'shared_translation_completed': True,
                      'owned_model_not_started': True, 'shared_model_retained_after_pause': True}
    finally:
        manager.disable()
        if loaded_for_test:
            request('/models/unload', {'model': TARGET_SHARED_MODEL})
            result['test_loaded_shared_model_restored'] = True
    (ROOT / 'runtime/shared-model-acceptance.json').write_text(json.dumps(result, indent=2), 'utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()

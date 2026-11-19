import json
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from double_ctrl_translator.config import Config
from double_ctrl_translator.endpoint import resolve_endpoint, resolve_healthy_endpoint
from double_ctrl_translator.local_model import OwnedModel, LocalModelError
from double_ctrl_translator.manager import WorkerManager
from double_ctrl_translator.settings import Settings


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / 'hermes'
        self.env = patch.dict(os.environ, {'DOUBLECTRLTRANSLATOR_HOME': str(self.root / 'app'), 'HERMES_HOME': str(self.home)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_first_start_migrates_once_without_enabling_listener(self):
        old = self.home / 'plugins/selection-translate/config.json'
        old.parent.mkdir(parents=True)
        old.write_text('{"double_ctrl_ms":420}', 'utf-8')
        settings = Settings.load()
        self.assertFalse(settings.listening)
        self.assertTrue(settings.launch_with_hermes)
        self.assertEqual(settings.translation['double_ctrl_ms'], 420)
        self.assertEqual((self.root / 'app/legacy-config.backup.json').read_text('utf-8'), old.read_text('utf-8'))
        old.write_text('{"double_ctrl_ms":500}', 'utf-8')
        self.assertEqual(Settings.load().translation['double_ctrl_ms'], 420)

    def test_discovery_and_explicit_missing_override(self):
        runtime = self.home / 'runtimes/llamacpp/new/cuda/llama-server.exe'
        runtime.parent.mkdir(parents=True)
        runtime.touch()
        settings = Settings.load()
        model, found = settings.resources()
        self.assertEqual(found, runtime)
        self.assertEqual(model.name, Config.model)
        settings.runtime_path = str(self.root / 'missing.exe')
        self.assertEqual(settings.resources()[1], self.root / 'missing.exe')

    def test_endpoint_uses_local_state_without_hermes_python(self):
        path = self.home / 'runtimes/llamacpp/server.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'pid': 123, 'base_url': 'http://127.0.0.1:18434/v1', 'api_key': 'private'}), 'utf-8')
        process = Mock()
        process.name.return_value = 'llama-server.exe'
        with patch('psutil.Process', return_value=process):
            self.assertEqual(resolve_endpoint()['base_url'], 'http://127.0.0.1:18434/v1')
        path.write_text(json.dumps({'pid': 123, 'base_url': 'https://example.com'}), 'utf-8')
        with patch('psutil.Process', return_value=process):
            with self.assertRaises(RuntimeError):
                resolve_endpoint()

    def test_shared_model_must_be_loaded_and_exact(self):
        endpoint = {'base_url': 'http://127.0.0.1:18434/v1', 'api_key': 'private'}
        response = Mock()
        response.status = 200
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch('double_ctrl_translator.endpoint.resolve_endpoint', return_value=endpoint), patch('double_ctrl_translator.endpoint._open_models', return_value=response):
            for name, status in [('Qwen3.5-4B-Q4_K_M', 'unloaded'), ('other', 'loaded')]:
                response.read.return_value = json.dumps({'data': [{'id': name, 'status': status}]}).encode()
                with self.assertRaises(RuntimeError):
                    resolve_healthy_endpoint()
            response.read.return_value = b'{"data":[{"id":"Qwen3.5-4B-Q4_K_M","status":"loaded"}]}'
            self.assertEqual(resolve_healthy_endpoint()['model_state'], 'loaded')

    def test_pause_cancels_and_stops_owned_model_without_changing_preference(self):
        settings = Settings.load()
        settings.listening = True
        settings.save()
        manager = WorkerManager()
        manager.model = Mock()
        manager.source = 'shared'
        with patch.object(manager, '_stop_worker') as worker:
            result = manager.disable()
        worker.assert_called_once()
        manager.model.stop.assert_called_once()
        self.assertTrue(manager.cancel.is_set())
        self.assertEqual(result['state'], 'disabled')
        self.assertTrue(Settings.load().listening)

    def test_pre_cancelled_model_start_never_spawns(self):
        event = threading.Event()
        event.set()
        with patch('subprocess.Popen') as spawn:
            with self.assertRaises(LocalModelError):
                OwnedModel(self.root).start(Mock(), cancel=event)
        spawn.assert_not_called()

    def test_internal_stdio_forces_utf8_even_when_streams_already_exist(self):
        from double_ctrl_translator.launch import pipe_stdio
        stdin, stdout = Mock(), Mock()
        with patch('sys.stdin', stdin), patch('sys.stdout', stdout):
            pipe_stdio()
        stdin.reconfigure.assert_called_once_with(encoding='utf-8', errors='strict')
        stdout.reconfigure.assert_called_once_with(encoding='utf-8', errors='strict')

    def test_diagnostics_keep_draining_after_non_json_output(self):
        manager = WorkerManager()
        process = Mock()
        process.stdout = io.StringIO('native warning\n{"type":"diagnostics","hook_healthy":true}\n')
        manager.process = process
        manager._messages(process)
        self.assertTrue(manager.diagnostics['hook_healthy'])

    def test_shared_loss_cancels_endpoint_and_prepares_model_without_resending(self):
        manager = WorkerManager()
        manager.source = 'shared'
        manager.endpoint = {'base_url': 'http://127.0.0.1:1/v1'}
        manager.want_enabled = True
        manager.model = Mock()
        manager.model.running.return_value = False
        manager.model.process = None
        with patch.object(manager, '_worker_running', return_value=True), patch.object(manager, '_send') as send, patch('double_ctrl_translator.manager.resolve_healthy_endpoint', side_effect=RuntimeError()), patch('double_ctrl_translator.manager.threading.Thread') as task:
            result = manager.status()
        self.assertIsNone(manager.endpoint)
        self.assertIn('重新划词', result['notice'])
        task.return_value.start.assert_called_once()
        self.assertTrue(any(call.args[0].get('op') == 'endpoint' for call in send.call_args_list))
        self.assertFalse(any(call.args[0].get('op') == 'test' for call in send.call_args_list))

    def test_install_metadata_preserves_unrelated_values_and_uninstall_keeps_settings(self):
        from double_ctrl_translator.deployment import configure
        import yaml
        self.home.mkdir()
        config = self.home / 'config.yaml'
        config.write_text('unrelated: {a: 42}\nplugins:\n  enabled: [other]\n  disabled: [selection-translate]\n', 'utf-8')
        configure(self.home, self.root / 'DoubleCtrlTranslator.exe')
        loaded = yaml.safe_load(config.read_text('utf-8'))
        self.assertEqual(loaded['unrelated'], {'a': 42})
        self.assertEqual(loaded['plugins']['enabled'], ['other', 'selection-translate'])
        configure(self.home, self.root / 'DoubleCtrlTranslator.exe', uninstall=True)
        self.assertTrue((self.root / 'app/settings.json').exists())
        self.assertFalse((self.root / 'app/bridge.json').exists())

    def test_legacy_migration_stops_only_processes_in_owned_plugin_directory(self):
        from double_ctrl_translator.deployment import stop_legacy_processes
        owned, unrelated = Mock(), Mock()
        for pid, process in enumerate((owned, unrelated), 100):
            process.pid = pid
            process.info = {'cmdline': ['python', '-m', 'double_ctrl_translator.control_surface'], 'create_time': 1.0}
        owned.cwd.return_value = str(self.home / 'plugins/selection-translate')
        unrelated.cwd.return_value = str(self.root / 'another-project')
        with patch('psutil.process_iter', return_value=[owned, unrelated]), patch('double_ctrl_translator.process_tree.owned_tree', return_value=[(100, 1.0)]) as tree, patch('double_ctrl_translator.process_tree.stop_owned') as stop:
            stop_legacy_processes(self.home)
        tree.assert_called_once_with(100, 1.0)
        stop.assert_called_once_with([(100, 1.0)])


if __name__ == '__main__':
    unittest.main()

import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import shutil
import subprocess
import tempfile

from double_ctrl_translator.endpoint import TARGET_SHARED_MODEL, resolve_healthy_endpoint
from double_ctrl_translator.manager import WorkerManager
from double_ctrl_translator.worker import request_model


class _Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class SharedEndpointTests(unittest.TestCase):
    def _models(self, state, model=TARGET_SHARED_MODEL):
        return _Response(json.dumps({"data": [{"id": model, "status": {"value": state}}]}).encode())

    def test_router_alive_but_target_unloaded_is_not_ready(self):
        endpoint = {"base_url": "http://127.0.0.1:18434/v1", "api_key": "memory-only"}
        with patch("double_ctrl_translator.endpoint.resolve_endpoint", return_value=endpoint), \
             patch("double_ctrl_translator.endpoint._open_models", return_value=self._models("unloaded")):
            with self.assertRaisesRegex(RuntimeError, "未加载"):
                resolve_healthy_endpoint()

    def test_loaded_shared_alias_is_forwarded_without_gguf(self):
        endpoint = {"base_url": "http://127.0.0.1:18434/v1", "api_key": "memory-only"}
        with patch("double_ctrl_translator.endpoint.resolve_endpoint", return_value=endpoint), \
             patch("double_ctrl_translator.endpoint._open_models", return_value=self._models("loaded")):
            result = resolve_healthy_endpoint()
        self.assertEqual(result["model"], TARGET_SHARED_MODEL)
        self.assertNotIn(".gguf", result["model"])

    def test_loaded_different_model_is_never_selected(self):
        endpoint = {"base_url": "http://127.0.0.1:18434/v1"}
        with patch("double_ctrl_translator.endpoint.resolve_endpoint", return_value=endpoint), \
             patch("double_ctrl_translator.endpoint._open_models", return_value=self._models("loaded", "Qwen3.5-27B")):
            with self.assertRaisesRegex(RuntimeError, "4B"):
                resolve_healthy_endpoint()

    def test_worker_uses_validated_shared_alias_and_rejects_27b(self):
        self.assertEqual(request_model({"model": TARGET_SHARED_MODEL}, "ignored.gguf"), TARGET_SHARED_MODEL)
        with self.assertRaisesRegex(Exception, "4B"):
            request_model({"model": "Qwen3.5-27B"}, "Qwen3.5-4B-Q4_K_M.gguf")


class EnableAndLoadTests(unittest.TestCase):
    def test_single_action_starts_owned_model_only_when_shared_is_unready(self):
        manager = WorkerManager(Path.cwd())
        manager.sessions.add("session")
        with patch.object(manager, "enable", return_value={"enabled": True, "state": "needs_model"}), \
             patch.object(manager, "start_model", return_value={"enabled": True, "state": "running", "model": "project"}) as start:
            state = manager.enable_and_load()
        self.assertEqual(state["model"], "project")
        start.assert_called_once_with()

    def test_single_action_reuses_ready_shared_model(self):
        manager = WorkerManager(Path.cwd())
        manager.sessions.add("session")
        ready = {"enabled": True, "state": "running", "model": "shared"}
        manager.worker_endpoint = {"base_url": "http://127.0.0.1:1/v1", "model": TARGET_SHARED_MODEL}
        with patch.object(manager, "enable", return_value=ready), \
             patch.object(manager, "start_model") as start, \
             patch("double_ctrl_translator.manager.warmup",
                   return_value={"warmup_ms": 12, "warmup_first_token_ms": 7}) as run_warmup, \
             patch.object(manager, "status", return_value=ready):
            self.assertIs(manager.enable_and_load(), ready)
        start.assert_not_called()
        run_warmup.assert_called_once()


class DesktopSurfaceTests(unittest.TestCase):
    def test_route_sidebar_chip_and_palette_all_reach_control_page(self):
        if not shutil.which("node"):
            self.skipTest("Node is required for the real plugin behavior test")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "package.json").write_text('{"type":"module"}', encoding="utf-8")
            for package in ("react", "@hermes/plugin-sdk"):
                folder = root / "node_modules" / package
                folder.mkdir(parents=True)
                (folder / "package.json").write_text('{"type":"module","exports":{".":"./index.js","./jsx-runtime":"./jsx-runtime.js"}}', encoding="utf-8")
            (root / "node_modules/react/index.js").write_text("export default {useState:x=>[x,()=>{}],useCallback:x=>x,useEffect:()=>{}}", encoding="utf-8")
            (root / "node_modules/react/jsx-runtime.js").write_text("export const jsx=(t,p)=>typeof t==='function'?t(p):({t,p}); export const jsxs=jsx", encoding="utf-8")
            (root / "node_modules/@hermes/plugin-sdk/index.js").write_text(
                "export const STATUSBAR_AREAS={right:'status'}; export const PALETTE_AREA='palette'; export const ROUTES_AREA='routes'; export const SIDEBAR_NAV_AREA='nav'; export const host={calls:[],navigate(p){this.calls.push(p)}}", encoding="utf-8")
            shutil.copyfile("plugin/desktop/plugin.js", root / "plugin.js")
            script = """
              import plugin from './plugin.js'; import {host} from '@hermes/plugin-sdk';
              const items=[]; const ctx={rest:async()=>({}),socket(){},onDispose(){},registerMany(x){items.push(...x)}};
              plugin.register(ctx);
              const route=items.find(x=>x.area==='routes'), nav=items.find(x=>x.area==='nav');
              const chip=items.find(x=>x.area==='status').render(); const palette=items.find(x=>x.area==='palette');
              chip.p.onClick(); palette.data.run();
              if(route.data.path!=='/selection-translate'||nav.data.to!=='/selection-translate'||host.calls.length!==2||host.calls.some(x=>x!=='/selection-translate')) process.exit(2);
            """
            result = subprocess.run(["node", "--input-type=module", "-e", script], cwd=root,
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

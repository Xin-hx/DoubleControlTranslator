from __future__ import annotations

import json
import urllib.error
import urllib.request

from .model import TranslationError, _validate_loopback


TARGET_SHARED_MODEL = "Qwen3.5-4B-Q4_K_M"
_READY_STATES = {"loaded", "running"}


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_models_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirect())


def resolve_endpoint() -> dict:
    """Read optional Hermes state without importing the Hermes Python environment."""
    import psutil
    from .settings import Settings
    from pathlib import Path
    try:
        home = Path(Settings.load().hermes_home)
        state = json.loads((home / "runtimes/llamacpp/server.json").read_text("utf-8"))
        process = psutil.Process(int(state.get("pid") or 0))
        if not process.is_running() or "llama" not in process.name().lower():
            raise ValueError("stale runtime state")
        base = str(state.get("base_url", ""))
        _validate_loopback(base)
        return {"base_url": base, "api_key": str(state.get("api_key", ""))}
    except (OSError, ValueError, TypeError, psutil.Error, TranslationError) as exc:
        raise RuntimeError("Hermes 共享模型服务未运行或状态无效") from exc


def _open_models(request: urllib.request.Request, timeout: float):
    return _models_opener.open(request, timeout=timeout)


def _state_value(item: dict) -> str:
    value = item.get("status", "")
    if isinstance(value, dict):
        value = value.get("value", "")
    return str(value).strip().lower()


def resolve_healthy_endpoint(timeout_s: float = 0.6) -> dict:
    """Return only a loaded exact 4B shared route, never a merely-live router."""
    endpoint = resolve_endpoint()
    base = str(endpoint.get("base_url", "")).rstrip("/")
    try:
        _validate_loopback(base)
        headers = {"Accept": "application/json"}
        key = endpoint.get("api_key")
        if isinstance(key, str) and key:
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(f"{base}/models", headers=headers, method="GET")
        with _open_models(request, timeout_s) as response:
            if getattr(response, "status", 200) != 200:
                raise RuntimeError("Hermes 模型列表请求失败")
            payload = json.loads(response.read(256 * 1024))
    except (TranslationError, OSError, ValueError, json.JSONDecodeError,
            urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise RuntimeError("Hermes 本机模型路由存在但模型状态不可用") from exc

    entries = payload.get("data", []) if isinstance(payload, dict) else []
    match = next((item for item in entries if isinstance(item, dict) and
                  item.get("id") == TARGET_SHARED_MODEL), None)
    if match is None:
        raise RuntimeError("Hermes 共享路由未提供指定的 4B 模型")
    state = _state_value(match)
    if state not in _READY_STATES:
        if state in {"unloaded", "loading", "failed", "error"}:
            raise RuntimeError(f"Hermes 共享 4B 模型未加载（{state}）")
        raise RuntimeError("Hermes 共享 4B 模型状态不明确")
    result = dict(endpoint)
    result["model"] = TARGET_SHARED_MODEL
    result["model_state"] = state
    return result

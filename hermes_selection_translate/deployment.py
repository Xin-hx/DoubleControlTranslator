"""Non-GUI installation metadata command used by the PowerShell installer."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import yaml
from .settings import Settings, data_dir
from .config import Config
from dataclasses import asdict


def stop_legacy_processes(home: Path):
    """Release old plugin working-directory locks without touching the Hermes host."""
    import psutil
    from .process_tree import owned_tree, stop_owned
    root = (home / 'plugins/selection-translate').resolve()
    modules = {'hermes_selection_translate.control_surface', 'hermes_selection_translate.worker',
               'hermes_selection_translate.model_guard'}
    for process in psutil.process_iter(['name', 'cmdline', 'create_time']):
        try:
            args = process.info['cmdline'] or []
            if not any(module in args for module in modules) or '-m' not in args:
                continue
            if Path(process.cwd()).resolve() != root:
                continue
            stop_owned(owned_tree(process.pid, process.info['create_time']))
        except (psutil.Error, OSError):
            continue


def configure(home: Path, executable: Path, uninstall=False):
    if not uninstall:
        stop_legacy_processes(home)
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    config_path = home / "config.yaml"
    if config_path.exists():
        original = config_path.read_text("utf-8")
        config = yaml.safe_load(original) or {}
        if not isinstance(config, dict):
            raise ValueError("Hermes config.yaml 不是映射，未修改")
        plugins = config.setdefault("plugins", {})
        if not isinstance(plugins, dict):
            raise ValueError("Hermes plugins 配置不是映射，未修改")
        for key in ("enabled", "disabled"):
            values = plugins.get(key) or []
            if not isinstance(values, list):
                raise ValueError("Hermes plugins 开关不是列表，未修改")
            plugins[key] = [value for value in values if value != "selection-translate"]
        if not uninstall:
            plugins["enabled"].append("selection-translate")
        backup = directory / ("hermes-config-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".yaml.bak")
        backup.write_text(original, "utf-8")
        temp = config_path.with_suffix(".hermestool.tmp")
        temp.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), "utf-8")
        temp.replace(config_path)
    if uninstall:
        (directory / "bridge.json").unlink(missing_ok=True)
        return
    settings = Settings.load()
    legacy = home / "plugins/selection-translate/config.json"
    if not (directory / "legacy-config.backup.json").exists() and legacy.is_file():
        settings.translation = asdict(Config.load(legacy))
        shutil.copy2(legacy, directory / "legacy-config.backup.json")
    settings.hermes_home = str(home)
    settings.save()
    (directory / "bridge.json").write_text(json.dumps({"executable": str(executable)}, ensure_ascii=False), "utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hermes-home", type=Path, required=True)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()
    configure(args.hermes_home, args.executable, args.uninstall)
    return 0

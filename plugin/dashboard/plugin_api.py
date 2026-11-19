"""Optional Hermes bridge: launch the independently installed desktop application."""
from pathlib import Path
import importlib.util
from fastapi import APIRouter

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("doublectrltranslator_startup_bridge", ROOT / "launcher.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)

router = APIRouter()
bridge.launch_from_hermes()

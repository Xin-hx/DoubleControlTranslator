from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from hermes_selection_translate.qt_ui import app_icon

app = QApplication([])
path = Path(__file__).resolve().parents[1] / 'build/hermestool.ico'
path.parent.mkdir(exist_ok=True)
if not app_icon().pixmap(64, 64).save(str(path), 'ICO'):
    raise RuntimeError('Could not generate application icon')

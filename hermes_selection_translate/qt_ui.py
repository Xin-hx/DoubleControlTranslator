from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QPalette
from PySide6.QtWidgets import QApplication


class Dispatcher(QObject):
    posted = Signal(int, object)

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.posted.connect(self.schedule, Qt.ConnectionType.QueuedConnection)

    @Slot(int, object)
    def schedule(self, delay, callback):
        QTimer.singleShot(delay, callback)

    def after(self, delay, callback):
        self.posted.emit(delay, callback)

    def mainloop(self):
        return self.app.exec()

    def destroy(self):
        self.app.quit()


def app_icon() -> QIcon:
    image = QPixmap(64, 64)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#5266eb"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 18, 18)
    painter.setPen(QColor("#ffffff"))
    painter.setFont(QFont("Microsoft YaHei UI", 27, QFont.Weight.Bold))
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "译")
    painter.end()
    return QIcon(image)


def apply_style(app):
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    dark = app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    bg, surface, text, muted, border = (("#171a23", "#232735", "#eef0f7", "#a0a8bd", "#363e52")
                                        if dark else ("#f5f6fa", "#ffffff", "#20273d", "#67738e", "#e0e4ee"))
    palette = app.palette()
    for role, color in ((QPalette.ColorRole.Window, bg), (QPalette.ColorRole.Base, surface),
                        (QPalette.ColorRole.Text, text), (QPalette.ColorRole.WindowText, text),
                        (QPalette.ColorRole.Button, surface), (QPalette.ColorRole.ButtonText, text)):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setWindowIcon(app_icon())
    app.setStyleSheet(f"""
        QWidget {{ color: {text}; }}
        QMainWindow, QDialog {{ background: {bg}; }}
        QFrame#surface {{ background: {surface}; border: 1px solid {border}; border-radius: 14px; }}
        QLabel#eyebrow, QLabel#muted {{ color: {muted}; }}
        QLabel#title {{ font-size: 23px; font-weight: 700; }}
        QLabel#phase {{ font-size: 18px; font-weight: 600; }}
        QPushButton {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; padding: 8px 14px; }}
        QPushButton:hover {{ border-color: #7181ed; }}
        QPushButton#primary {{ background: #5266eb; color: white; border: none; font-weight: 600; }}
        QPushButton#primary:hover {{ background: #4358df; }}
        QPushButton:disabled {{ color: {muted}; background: {bg}; }}
        QLineEdit, QTextEdit, QPlainTextEdit {{ background: {surface}; border: 1px solid {border}; border-radius: 8px; padding: 7px; }}
        QProgressBar {{ border: none; background: {border}; border-radius: 3px; max-height: 6px; }}
        QProgressBar::chunk {{ background: #5266eb; border-radius: 3px; }}
        QToolButton {{ border: none; padding: 6px 0; color: {muted}; }}
        QMenu {{ background: {surface}; border: 1px solid {border}; padding: 5px; }}
        QMenu::item {{ padding: 7px 24px; border-radius: 4px; }}
        QMenu::item:selected {{ background: #5266eb; color: white; }}
    """)

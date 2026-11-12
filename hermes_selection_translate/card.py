from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout


class TranslationCard:
    def __init__(self, root, on_cancel):
        self.on_cancel = on_cancel
        self.win = None
        self.text = ""
        self.hwnd = 0

    def show(self, x: int, y: int, state: str = "翻译中…") -> None:
        if self.win:
            self.win.close()
        self.text = ""
        win = QFrame(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        win.setObjectName("surface")
        win.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        win.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus)
        win.setWindowTitle("HermesTool · 译文")
        layout = QVBoxLayout(win)
        layout.setContentsMargins(18, 14, 18, 14)
        header = QHBoxLayout()
        title = QLabel("划词翻译")
        title.setStyleSheet("font-weight: 600;")
        header.addWidget(title)
        header.addStretch()
        close = QPushButton("✕")
        close.setFixedSize(30, 30)
        close.setStyleSheet("padding: 0; border: none;")
        close.clicked.connect(self.close)
        header.addWidget(close)
        layout.addLayout(header)
        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlainText(state)
        self.output.setStyleSheet("border: none; font-size: 14px;")
        layout.addWidget(self.output)
        footer = QHBoxLayout()
        hint = QLabel("Esc 关闭")
        hint.setObjectName("muted")
        footer.addWidget(hint)
        footer.addStretch()
        self.copy_button = QPushButton("复制译文")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self.copy)
        footer.addWidget(self.copy_button)
        layout.addLayout(footer)
        screen = QApplication.screenAt(QPoint(x, y)) or QApplication.primaryScreen()
        rect = screen.availableGeometry()
        win.resize(min(440, rect.width()), min(300, rect.height()))
        win.move(max(rect.left(), min(x + 12, rect.right() - win.width() + 1)),
                 max(rect.top(), min(y + 18, rect.bottom() - win.height() + 1)))
        self.win = win
        win.show()
        self.hwnd = int(win.winId())

    def update(self, text: str, state: str = "success") -> None:
        if not self.win:
            return
        self.text = text if state == "success" else ""
        self.output.setPlainText(text if state in {"success", "progress"} else f"{state}\n{text}")
        self.copy_button.setEnabled(bool(self.text))

    def copy(self) -> None:
        if self.text:
            QApplication.clipboard().setText(self.text)

    def close(self) -> None:
        self.on_cancel()
        if self.win:
            self.win.close()
            self.win.deleteLater()
            self.win = None
            self.hwnd = 0

    def visible(self) -> bool:
        return bool(self.win and self.win.isVisible())

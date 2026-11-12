from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import sys
import threading

import psutil
import yaml
from PySide6.QtCore import QLockFile, QObject, QTimer, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget)

from .manager import WorkerManager
from .qt_ui import apply_style, app_icon
from .settings import Settings, data_dir
from .privileges import privilege_status, process_elevated, launch_elevated, wait_for_previous_instance


class Tasks(QObject):
    completed = Signal(str, object)

    def run(self, name, action):
        def work():
            try:
                value = action()
            except Exception as exc:
                logging.exception("Task %s failed", name)
                value = {"error": str(exc)}
            self.completed.emit(name, value)
        threading.Thread(target=work, name=name, daemon=True).start()


def environment():
    settings = Settings.load()
    home = Path(settings.hermes_home)
    model, runtime = settings.resources()
    installed = (home / "plugins/selection-translate/dashboard/plugin_api.py").is_file()
    enabled = False
    try:
        config = yaml.safe_load((home / "config.yaml").read_text("utf-8")) or {}
        plugins = config.get("plugins") or {}
        enabled = "selection-translate" in (plugins.get("enabled") or []) and "selection-translate" not in (plugins.get("disabled") or [])
    except (OSError, ValueError, yaml.YAMLError, AttributeError):
        pass
    running = any(p.info["name"] and p.info["name"].lower() == "hermes.exe" for p in psutil.process_iter(["name"]))
    from .endpoint import resolve_healthy_endpoint
    try:
        resolve_healthy_endpoint()
        shared_ready = True
    except RuntimeError:
        shared_ready = False
    return {"model": str(model or "未找到"), "model_ok": bool(model and model.is_file()),
            "runtime": str(runtime or "未找到"), "runtime_ok": bool(runtime and runtime.is_file()),
            "hermes_installed": home.is_dir(), "hermes_running": running,
            "bridge_installed": installed, "bridge_enabled": enabled,
            "launch": settings.launch_with_hermes, "home": str(home), "shared_ready": shared_ready,
            **privilege_status()}


class SettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("设置 · HermesTool")
        self.resize(600, 280)
        self.settings = Settings.load()
        layout = QVBoxLayout(self)
        note = QLabel("留空模型与运行时路径时，自动发现 Hermes 已有资源。")
        note.setObjectName("muted")
        layout.addWidget(note)
        form = QFormLayout()
        self.fields = {}
        for key, title, folder in (("hermes_home", "Hermes 目录", True), ("model_path", "Qwen 4B GGUF", False),
                                    ("runtime_path", "llama-server.exe", False)):
            row = QHBoxLayout()
            edit = QLineEdit(getattr(self.settings, key))
            edit.setPlaceholderText("自动发现" if key != "hermes_home" else "Hermes 数据目录")
            self.fields[key] = edit
            row.addWidget(edit)
            browse = QPushButton("浏览")
            browse.clicked.connect(lambda _=False, e=edit, f=folder, k=key: self.browse(e, f, k))
            row.addWidget(browse)
            form.addRow(title, row)
        layout.addLayout(form)
        self.launch = QCheckBox("随 Hermes 启动（需要已启用启动桥接插件）")
        self.launch.setChecked(self.settings.launch_with_hermes)
        layout.addWidget(self.launch)
        info = QLabel("资源路径更改将在下次开启翻译时生效。")
        info.setObjectName("muted")
        layout.addWidget(info)
        access = QLabel("当前以管理员权限运行，可兼容管理员应用。" if process_elevated() else
                        "当前为普通权限；管理员运行的 VS Code 等应用需要匹配权限。")
        access.setWordWrap(True)
        access.setObjectName("muted")
        layout.addWidget(access)
        if not process_elevated():
            restart = QPushButton("以管理员身份重启")
            restart.clicked.connect(self.restart_elevated)
            layout.addWidget(restart)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def restart_elevated(self):
        self.reject()
        self.parent().restart_elevated()

    def browse(self, edit, folder, key):
        if folder:
            path = QFileDialog.getExistingDirectory(self, "选择 Hermes 目录", edit.text())
        else:
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", edit.text(),
                "Qwen GGUF (*.gguf)" if key == "model_path" else "llama-server (llama-server.exe)")
        if path:
            edit.setText(path)

    def save(self):
        for key, edit in self.fields.items():
            value = edit.text().strip()
            path = Path(value)
            if value and not (path.is_dir() if key == "hermes_home" else path.is_file()):
                QMessageBox.warning(self, "路径不可用", f"请检查：{value}")
                return
            if key == "model_path" and value and path.name != "Qwen3.5-4B-Q4_K_M.gguf":
                QMessageBox.warning(self, "模型不匹配", "本版本仅支持 Qwen3.5-4B-Q4_K_M.gguf")
                return
            if key == "runtime_path" and value and path.name.lower() != "llama-server.exe":
                QMessageBox.warning(self, "运行时不匹配", "请选择 llama-server.exe")
                return
            setattr(self.settings, key, value)
        # Preserve a listening toggle changed while this dialog was open.
        self.settings.listening = Settings.load().listening
        self.settings.launch_with_hermes = self.launch.isChecked()
        self.settings.save()
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.manager = WorkerManager()
        self.tasks = Tasks()
        self.tasks.completed.connect(self.complete)
        self.state = {"enabled": False, "state": "disabled", "model": "none"}
        self.env = {}
        self.pending = False
        self.polling = False
        self.quitting = False
        self.elevation_mismatch_seen = False
        self.setWindowTitle("HermesTool · 划词翻译")
        self.resize(560, 460)
        self.setMinimumSize(520, 420)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(14)
        top = QHBoxLayout()
        title_col = QVBoxLayout()
        eyebrow = QLabel("HERMESTOOL  /  LOCAL TRANSLATION")
        eyebrow.setObjectName("eyebrow")
        title_col.addWidget(eyebrow)
        title = QLabel("划词，即译")
        title.setObjectName("title")
        title_col.addWidget(title)
        top.addLayout(title_col)
        top.addStretch()
        settings = QPushButton("设置")
        settings.clicked.connect(self.settings)
        top.addWidget(settings)
        exit_button = QPushButton("退出")
        exit_button.clicked.connect(self.quit)
        exit_button.setVisible(not QSystemTrayIcon.isSystemTrayAvailable())
        top.addWidget(exit_button)
        layout.addLayout(top)
        surface = QFrame()
        surface.setObjectName("surface")
        box = QVBoxLayout(surface)
        box.setContentsMargins(18, 16, 18, 16)
        box.setSpacing(10)
        self.phase = QLabel("已暂停")
        self.phase.setObjectName("phase")
        box.addWidget(self.phase)
        self.summary = QLabel("选中文字，连续轻按两次 Ctrl")
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        box.addWidget(self.summary)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.hide()
        box.addWidget(self.progress)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setObjectName("muted")
        self.message.hide()
        box.addWidget(self.message)
        self.permission = QPushButton("检测到管理员应用 · 以管理员身份重启")
        self.permission.setToolTip("普通权限无法完整监听或读取管理员应用。也可以用普通权限重新打开目标应用。")
        self.permission.clicked.connect(self.restart_elevated)
        self.permission.hide()
        box.addWidget(self.permission)
        row = QHBoxLayout()
        self.primary = QPushButton("开启翻译")
        self.primary.setObjectName("primary")
        self.primary.clicked.connect(self.toggle)
        row.addWidget(self.primary)
        self.release = QPushButton("释放模型")
        self.release.clicked.connect(lambda: self.action("release", self.manager.stop_model))
        row.addWidget(self.release)
        self.test = QPushButton("试译")
        self.test.clicked.connect(lambda: self.action("test", self.manager.test_card))
        row.addWidget(self.test)
        box.addLayout(row)
        layout.addWidget(surface)
        hint = QLabel("双 Ctrl 翻译  ·  Esc 关闭浮卡  ·  关闭窗口后驻留托盘")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        layout.addWidget(hint)
        self.details_toggle = QToolButton()
        self.details_toggle.setText("环境与运行详情")
        self.details_toggle.setCheckable(True)
        self.details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.details_toggle.toggled.connect(self.show_details)
        layout.addWidget(self.details_toggle)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(160)
        self.details.hide()
        layout.addWidget(self.details)
        layout.addStretch()
        self.footer = QLabel("本机推理 · Qwen 4B · 文本仅发送到本机模型")
        self.footer.setObjectName("muted")
        layout.addWidget(self.footer)
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        menu.addAction("打开窗口", self.restore)
        self.tray_toggle = menu.addAction("开启翻译", self.toggle)
        self.tray_release = menu.addAction("释放项目模型", lambda: self.action("release", self.manager.stop_model))
        menu.addSeparator()
        menu.addAction("退出", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.restore() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        self.refresh()
        if Settings.load().listening:
            QTimer.singleShot(0, lambda: self.set_listening(True))

    def show_details(self, checked):
        self.details.setVisible(checked)
        self.details_toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self.resize(560, 640 if checked else 460)

    def settings(self):
        if SettingsDialog(self).exec():
            self.refresh()

    def restart_elevated(self):
        try:
            launch_elevated()
        except Exception as exc:
            # A declined UAC prompt must leave the current listener and model running.
            if getattr(exc, 'winerror', None) != 1223:
                QMessageBox.warning(self, "未能重启", str(exc))
            return
        self.quit()

    def restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self.quitting:
            event.accept()
        elif self.tray.isVisible():
            event.ignore()
            self.hide()
        else:
            event.ignore()
            self.message.setText("系统托盘不可用，窗口保持打开；请点击右上角“退出”结束程序。")
            self.message.show()
            self.restore()

    def toggle(self):
        self.set_listening(not self.manager.want_enabled)

    def set_listening(self, enabled):
        if self.quitting:
            return
        settings = Settings.load()
        settings.listening = enabled
        settings.save()
        self.action("enable" if enabled else "disable", self.manager.enable_and_load if enabled else self.manager.disable)

    def action(self, name, callback):
        if self.quitting or (self.pending and name not in {"disable", "release"}):
            return
        self.pending = True
        self.tasks.run(name, callback)
        self.render()

    def refresh(self):
        if self.polling or self.quitting:
            return
        self.polling = True
        self.tasks.run("poll", lambda: {"status": self.manager.status(), "environment": environment()})

    def complete(self, name, value):
        if name == "quit":
            self.tray.hide()
            self.app.quit()
            return
        if self.quitting:
            return
        if name == "poll":
            self.polling = False
            if "status" in value:
                self.state = value["status"]
                self.env = value["environment"]
            else:
                self.state = {**self.state, "error": value.get("error")}
        else:
            self.pending = self.manager.busy
            # Re-read after overlapping cancellation instead of applying stale task results.
            self.refresh()
        self.render()

    def render(self):
        state = self.state
        phase = state.get("state", "disabled")
        labels = {"disabled": "已暂停", "starting_model": "正在加载模型", "warming_model": "正在预热",
                  "running": "翻译已就绪", "needs_model": "等待模型", "error": "需要处理"}
        self.phase.setText(labels.get(phase, phase))
        source = {"shared": "Hermes 共享模型", "project": "独立模型", "none": "模型未加载"}.get(state.get("model"), "模型未加载")
        self.summary.setText(f"{source} · Qwen 4B" if state.get("enabled") else "选中文字，连续轻按两次 Ctrl")
        self.progress.setVisible(self.pending or phase in {"starting_model", "warming_model"})
        message = state.get("error") or state.get("notice") or ""
        self.message.setText(message)
        self.message.setVisible(bool(message))
        self.primary.setText("暂停翻译" if self.manager.want_enabled else "开启翻译")
        self.primary.setEnabled(not self.pending or self.manager.want_enabled)
        self.release.setEnabled(state.get("model") == "project" or phase in {"starting_model", "warming_model"})
        self.test.setEnabled(phase == "running" and not self.pending)
        self.tray_toggle.setText(self.primary.text())
        self.tray_toggle.setEnabled(self.primary.isEnabled())
        self.tray_release.setEnabled(self.release.isEnabled())
        self.tray.setToolTip("HermesTool · " + labels.get(phase, phase))
        e = self.env
        self.elevation_mismatch_seen |= bool(e.get('elevation_mismatch'))
        self.permission.setVisible(self.elevation_mismatch_seen and e.get('elevated') is not True)
        ok = lambda value: "满足" if value else "未满足"
        lines = ["独立翻译资源（共享模型可用时无需另行加载）",
                 f"Qwen 4B：{ok(e.get('model_ok'))}  ·  {e.get('model', '检查中')}",
                 f"llama.cpp：{ok(e.get('runtime_ok'))}  ·  {e.get('runtime', '检查中')}", "",
                 "可选 Hermes 联动（不影响独立运行）",
                 f"Hermes：{'已安装' if e.get('hermes_installed') else '未发现'} / {'运行中' if e.get('hermes_running') else '未运行'}",
                 f"启动插件：{'已安装' if e.get('bridge_installed') else '未安装'} / {'已启用' if e.get('bridge_enabled') else '未启用'}",
                 f"随 Hermes 启动：{'开启' if e.get('launch') else '关闭'}",
                 f"共享 Qwen 4B：{'正在使用' if state.get('model') == 'shared' else '已就绪' if e.get('shared_ready') else '未就绪'}"]
        d = state.get("diagnostics") or {}
        lines.append(f"程序权限：{'管理员' if e.get('elevated') else '普通'}")
        if self.elevation_mismatch_seen and not e.get('elevated'):
            lines.append("检测到管理员应用：普通权限无法完整监听或读取其选区，请匹配运行权限。")
        lines += ["", f"钩子：{'健康' if d.get('hook_healthy') else '关闭或等待'} · 双 Ctrl：{d.get('double_ctrl_triggers', 0)} · 选区失败：{d.get('selection_failures', 0)}"]
        for key, title in (("worker", "监听进程"), ("model", "模型进程")):
            if state.get(key + "_pid"):
                lines.append(f"{title} PID {state[key + '_pid']} · {state.get(key + '_rss_bytes', 0) // 1048576} MiB")
        if d.get("last_first_token_ms") is not None:
            lines.append(f"最近首字 {d['last_first_token_ms'] / 1000:.1f} 秒")
        if state.get("warmup_ms") is not None:
            lines.append(f"预热 {state['warmup_ms'] / 1000:.1f} 秒")
        self.details.setPlainText("\n".join(lines))

    def quit(self):
        if self.quitting:
            return
        self.quitting = True
        self.timer.stop()
        self.setEnabled(False)
        self.phase.setText("正在退出")
        # Keep the persisted listening preference for the next launch.
        self.tasks.run("quit", self.manager.disable)


def main():
    wait_for_previous_instance()
    app = QApplication(sys.argv)
    app.setApplicationName("HermesTool")
    app.setQuitOnLastWindowClosed(False)
    apply_style(app)
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "logs").mkdir(exist_ok=True)
    logging.basicConfig(filename=directory / "logs/app.log", encoding="utf-8", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = Settings.load()
    except Exception as exc:
        QMessageBox.critical(None, "设置读取失败", f"{exc}\n请检查 {directory / 'settings.json'}")
        return 1
    if "--from-hermes" in sys.argv and not settings.launch_with_hermes:
        return 0
    name = "HermesTool-" + hashlib.sha256(str(directory.resolve()).lower().encode()).hexdigest()[:16]
    lock = QLockFile(str(directory / "instance.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        socket = QLocalSocket()
        socket.connectToServer(name)
        if socket.waitForConnected(3000):
            socket.write(b"quit" if "--quit" in sys.argv else b"show")
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
            return 0
        QMessageBox.warning(None, "HermesTool", "已有实例正在启动或退出，请稍后重试。")
        return 1
    if "--quit" in sys.argv:
        lock.unlock()
        return 0
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    QLocalServer.removeServer(name)
    if not server.listen(name):
        QMessageBox.critical(None, "启动失败", server.errorString())
        return 1
    window = MainWindow(app)
    connections = set()
    def command(connection):
        message = bytes(connection.readAll())[:16]
        if message == b"quit":
            window.quit()
        elif message == b"show":
            window.restore()
        connection.disconnectFromServer()
    def forget(connection):
        connections.discard(connection)
        connection.deleteLater()
    def incoming():
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            connections.add(connection)
            connection.readyRead.connect(lambda c=connection: command(c))
            connection.disconnected.connect(lambda c=connection: forget(c))
            if connection.bytesAvailable():
                command(connection)
    server.newConnection.connect(incoming)
    app.aboutToQuit.connect(server.close)
    app.styleHints().colorSchemeChanged.connect(lambda _: apply_style(app))
    window.show()
    result = app.exec()
    lock.unlock()
    return result

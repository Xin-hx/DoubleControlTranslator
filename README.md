# HermesTool 独立划词翻译

Windows 桌面程序，使用 PySide6 界面与系统托盘，通过本机 llama.cpp 运行 Qwen3.5-4B-Q4_K_M。Hermes 是可选的资源来源与启动入口；程序无需 Hermes 后端或其 Python 环境即可工作。

## 运行与安装

开发运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m hermes_selection_translate
```

构建、安装：

```powershell
.\scripts\build.ps1
.\scripts\install.ps1
```

构建产物是 `dist\HermesTool\HermesTool.exe`，分发时必须保留整个 `HermesTool` 目录。安装到 `%LOCALAPPDATA%\Programs\HermesTool`，创建桌面与开始菜单快捷方式。安装包不包含 GGUF、llama.cpp 或 CUDA 运行库；这些资源从 Hermes 自动发现，或在设置中手动选择。独立 exe 不要求目标机器安装 Python。

安装器只替换带本项目标记的旧插件，并备份迁移旧翻译配置；保留其他插件和模型文件。Hermes 配置会保留原始备份及所有无关配置值，YAML 注释与格式可能重新排版。安装后重启 Hermes，启动桥接生效。没有 Hermes 时仍可安装和独立运行。

更新前从托盘退出程序，再执行 `scripts\update.ps1`。卸载使用 `scripts\uninstall.ps1`，默认保留用户设置、日志、模型及 llama.cpp。自定义安装位置可使用脚本参数 `-InstallDir`、`-HermesHome`，卸载时传入相同路径。

## 使用

- 首次打开时点击“开启翻译”，以后按上次监听开关恢复。准备模型需要加载与预热，请等“翻译已就绪”后划词。
- 在其他应用选中文字，350 毫秒内连续轻按并释放两次 Ctrl。浮卡不抢前台焦点；Esc 或关闭按钮取消当前翻译，译文完成后可以复制。
- 关闭主窗口收起到托盘；双击托盘恢复。托盘菜单可开启／暂停、释放项目模型及退出。系统托盘不可用时保持窗口可见。
- 暂停会停止监听并释放程序启动的模型。退出也会清理自有进程，但保留上次监听偏好。共享模型不由本程序卸载。
- 默认随 Hermes 启动并显示窗口；Hermes 退出后程序继续运行。“设置”中可以关闭联动，或更改 Hermes、GGUF 与 llama-server 路径。路径更改在下次开启时生效。
- 主窗口“环境与运行详情”区分独立翻译资源和可选 Hermes 条件。共享 Qwen 必须精确匹配并已加载才会复用；否则启动独立模型，默认端口 18435。端口占用时报告错误，不清理其他服务。
- 共享服务断开时取消当前请求并准备独立模型；就绪后需要重新划词，不自动重发原选区。
- 若 VS Code 等目标应用以管理员身份运行，普通权限的翻译程序可能无法接收按键或读取选区。程序会提示权限不匹配，可在主窗口提示或“设置”中选择“以管理员身份重启”，并由你确认 Windows 提示；也可将目标应用改为普通权限运行。取消提升不会关闭当前程序，程序不修改系统安全设置或永久提权。

设置与日志位于 `%LOCALAPPDATA%\HermesTool`。默认最大选区 2000 字符、连接超时 3 秒、读取超时 20 秒、总时限 90 秒；高级参数保存在 `settings.json` 的 `translation` 中。API 密钥不在界面或诊断结果中显示。模型请求只允许本机 IP，禁用代理和重定向。

命令行 `HermesTool.exe --quit` 可请求当前用户的已有实例正常退出；再次运行 exe 会恢复已有窗口。`--worker`、`--model-guard` 是内部管道入口，不应直接运行。

## 架构

```text
可选 Hermes 启动桥接 ──> 独立 Qt 主程序（单实例、设置、托盘、管理器）
                              ├─ JSON 管道 ─> Qt Worker（键盘钩子、UIA／剪贴板、浮卡）
                              └─ JSON 管道 ─> 模型守护程序 ─> llama-server
                                                  Worker ── HTTP/SSE ─> 模型
```

桥接采用 Explorer 作为 Windows 父进程，避免 Hermes 关闭时的进程树清理影响独立程序。Worker 和守护程序绑定独立程序 PID、创建时间及控制管道；自有模型额外使用 kill-on-close Job Object。单实例通过用户范围的本地命名管道与锁文件实现。

## 必要验收

只运行本次变更相关检查：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_standalone.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_selection.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_behavior.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_config.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_qt_shell.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_privileges.py
.\.venv\Scripts\python.exe tests\integration_standalone.py
.\.venv\Scripts\python.exe tests\integration_bridge.py
```

独立链路验收需要已构建 exe、本地 GGUF 与 llama.cpp，使用独立的 Win32 测试窗口完成真实选区、真实推理、浮卡、取消和进程清理检查，不发送输入到用户应用。锁屏时只运行固定样例推理，报告中将真实划词和焦点检查标为未完成。桥接验收检查 Explorer 父进程、单实例和正常退出；真实 Hermes 重启联动仍需在桌面解锁后检查。`--allow-test-marker` 只用于显式测试，生产监听仍忽略注入按键。

需要验证真实宿主时，单独运行 `tests\integration_host_lifecycle.py`：它会正常关闭并重新打开 Hermes，检查自动唤起、退出后存活和脱离 Hermes 的真实翻译，结束时恢复 Hermes。`tests\integration_shared.py` 检查真实共享模型复用与暂停后保留；若测试临时加载了原本未加载的模型，会在结束时恢复其原始未加载状态。这两项不加入常规全量运行。

其余旧集成脚本记录的是原 Hermes 宿主／Tkinter 架构，本次不作为独立版本验收入口。

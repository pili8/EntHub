"""EntHub 状态栏菜单（macOS Menu Bar）
作为独立进程运行，与 Flask 主进程解耦。
通过 $HOME/Library/Application Support/EntHub/enthub.pid 与 Flask 通信。
"""
import os
import sys
import signal
import subprocess
import json
from pathlib import Path

import rumps
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

PID_FILE = Path.home() / "Library" / "Application Support" / "EntHub" / "enthub.pid"
LOG_FILE = Path.home() / "Library" / "Logs" / "EntHub.log"
CONFIG_FILE = Path(__file__).parent / "config.json"
URL = "http://127.0.0.1:5210"


def _read_config() -> dict:
    """读取项目根目录 config.json"""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _write_config(config: dict) -> None:
    """写入 config.json"""
    CONFIG_FILE.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _open(path: str) -> None:
    subprocess.Popen(["open", f"{URL}{path}"])


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def _is_flask_alive() -> bool:
    pid = _read_pid()
    if not pid:
        return False
    try:
        os.kill(pid, 0)  # signal 0 = check existence only
        return True
    except OSError:
        return False


# 状态栏图标路径（png 透明背景，rumps 会自动缩放到 22pt）
ICON_PATH = str(Path(__file__).parent / "static" / "menubar_icon.png")


class EntHubMenuBar(rumps.App):
    def __init__(self):
        super().__init__("EntHub", icon=ICON_PATH, quit_button=None)
        # 设为 Accessory：不进 Dock、不出现在 ⌘Tab 切换器
        # super().__init__ 内部会调用 NSApplication.sharedApplication()，
        # 这里必须用显式调用，不能用 pyobjc 的 NSApp 代理（此时还是 None）
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        # 状态指示项（不可点击）
        self.status_item = rumps.MenuItem("服务运行中 · 5210")
        self.status_item.set_callback(None)

        # 读取配置
        config = _read_config()
        auto_open = config.get("auto_open_web", False)
        mark = "✓" if auto_open else "✗"

        # 配置二级菜单
        self.auto_open_item = rumps.MenuItem(
            f"启动时自动打开Web {mark}", callback=self.on_toggle_auto_open
        )
        config_submenu = rumps.MenuItem("配置")
        config_submenu.add(self.auto_open_item)
        config_submenu.add(rumps.MenuItem("打开日志", callback=self.on_open_log))

        # 主菜单
        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("一键标注号码", callback=self.on_quick_annotate),
            None,
            rumps.MenuItem("打开控制台", callback=self.on_open_console),
            rumps.MenuItem("备份管理", callback=self.on_open_backup),
            config_submenu,
            None,
            rumps.MenuItem("重启服务", callback=self.on_restart),
            rumps.MenuItem("停止服务", callback=self.on_stop),
        ]

    # ── 菜单回调 ─────────────────────────────────────────────
    def on_open_console(self, _):  _open("/")
    def on_open_backup(self, _):   _open("/backup")
    def on_restart(self, _):       _open("/restart")

    def on_toggle_auto_open(self, _):
        """切换启动时是否自动打开浏览器"""
        config = _read_config()
        new_val = not config.get("auto_open_web", False)
        config["auto_open_web"] = new_val
        _write_config(config)
        mark = "✓" if new_val else "✗"
        self.auto_open_item.title = f"启动时自动打开Web {mark}"
        state = "开启" if new_val else "关闭"
        rumps.notification("EntHub", f"启动时自动打开Web已{state}", "")

    def on_quick_annotate(self, _):
        """一键标注：从剪贴板读取 → 标注号码 → 写回剪贴板"""
        import json
        import urllib.request
        import urllib.error

        print("[EntHub] 一键标注开始...", flush=True)

        # 1. 读剪贴板（macOS pbpaste）
        try:
            text = subprocess.check_output(["pbpaste"]).decode("utf-8")
        except Exception as e:
            print(f"[EntHub] 读取剪贴板失败: {e}", flush=True)
            self._notify_result("读取剪贴板失败", type="error")
            return

        if not text.strip():
            print("[EntHub] 剪贴板为空", flush=True)
            self._notify_result("剪贴板为空", type="warning")
            return

        print(f"[EntHub] 剪贴板内容长度: {len(text)} 字", flush=True)

        # 2. 调用本地 API
        try:
            req = urllib.request.Request(
                f"{URL}/api/phone_count_text",
                data=json.dumps({"text": text}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError:
            print("[EntHub] 服务未运行", flush=True)
            self._notify_result("服务未运行", type="error")
            return
        except Exception as e:
            print(f"[EntHub] API 调用失败: {e}", flush=True)
            self._notify_result("API 调用失败", type="error")
            return

        if data.get("code") != 0:
            msg = data.get("message", "未知错误")
            print(f"[EntHub] 标注失败: {msg}", flush=True)
            self._notify_result("标注失败", type="error")
            return

        annotated = data["data"]["annotated_text"]
        phone_count = data["data"]["phone_count"]

        print(f"[EntHub] 识别到 {phone_count} 个号码", flush=True)

        # 3. 写回剪贴板（macOS pbcopy）
        try:
            proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            proc.communicate(annotated.encode("utf-8"))
        except Exception as e:
            print(f"[EntHub] 写入剪贴板失败: {e}", flush=True)
            self._notify_result("写入剪贴板失败", type="error")
            return

        # 4. 多渠道反馈
        if phone_count == 0:
            self._notify_result("未发现电话号码", type="warning")
        else:
            self._notify_result(f"已标注 {phone_count} 个号码", type="success")

    def _notify_result(self, message, type="info"):
        """多渠道反馈：Toast 浮窗 + 系统通知 + 菜单标题短暂变化 + 终端日志"""
        # 1. 终端日志
        print(f"[EntHub] {message}", flush=True)

        # 2. Toast 浮窗（主反馈，屏幕居中，紧凑）
        try:
            from toast import show_toast
            show_toast(message, duration=1.8, type=type)
        except Exception as e:
            print(f"[EntHub] Toast 显示失败: {e}", flush=True)

        # 3. 系统通知（备用反馈）
        try:
            rumps.notification("EntHub", message, "")
        except Exception as e:
            print(f"[EntHub] 系统通知失败: {e}", flush=True)

        # 4. 菜单栏标题短暂变化（最次反馈）
        try:
            original_title = self.title
            icon = {"success": "✓", "warning": "⚠", "error": "✗", "info": "·"}.get(type, "·")
            self.title = f"EntHub {icon}"
            import threading
            def restore():
                import time
                time.sleep(1.5)
                self.title = original_title
            t = threading.Thread(target=restore, daemon=True)
            t.start()
        except Exception:
            pass

    def on_open_log(self, _):
        if LOG_FILE.exists():
            subprocess.Popen(["open", "-t", str(LOG_FILE)])
        else:
            rumps.notification("EntHub", "暂无日志", "服务可能尚未产生日志。")

    def on_stop(self, _):
        pid = _read_pid()
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
                # 给 2 秒优雅退出
                import time
                for _ in range(20):
                    time.sleep(0.1)
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        break
                else:
                    # 强杀
                    try: os.kill(pid, signal.SIGKILL)
                    except OSError: pass
            except OSError:
                pass

        if PID_FILE.exists():
            try: PID_FILE.unlink()
            except OSError: pass

        rumps.notification("EntHub", "服务已停止", "已停止 Flask 进程，状态栏菜单退出。")
        rumps.quit_application()


def main():
    if sys.platform != "darwin":
        print("menubar.py 仅支持 macOS（依赖 rumps / AppKit）", file=sys.stderr)
        sys.exit(1)

    # 给 Flask 1.5 秒启动
    import time
    time.sleep(1.5)

    if not _is_flask_alive():
        rumps.notification(
            "EntHub", "未检测到 Flask 进程",
            f"请检查 {LOG_FILE} 或手动启动 ./start.sh"
        )
        # 仍然打开菜单，让用户可以查看日志 / 重试
        # 但不强制退出，让用户操作

    EntHubMenuBar().run()


if __name__ == "__main__":
    main()

"""EntHub 状态栏菜单（macOS Menu Bar）
作为独立进程运行，与 Flask 主进程解耦。
通过 $HOME/Library/Application Support/EntHub/enthub.pid 与 Flask 通信。
"""
import os
import sys
import signal
import subprocess
from pathlib import Path

import rumps
from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

PID_FILE = Path.home() / "Library" / "Application Support" / "EntHub" / "enthub.pid"
LOG_FILE = Path.home() / "Library" / "Logs" / "EntHub.log"
URL = "http://127.0.0.1:5210"


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

        # 主菜单
        self.menu = [
            self.status_item,
            None,
            rumps.MenuItem("打开控制台", callback=self.on_open_console),
            rumps.MenuItem("数据管理", callback=self.on_open_data),
            rumps.MenuItem("电话统计", callback=self.on_open_stats),
            rumps.MenuItem("股东统计", callback=self.on_open_stats_shareholder),
            None,
            rumps.MenuItem("在 Finder 中查看数据", callback=self.on_open_data_dir),
            rumps.MenuItem("打开日志", callback=self.on_open_log),
            None,
            rumps.MenuItem("停止服务", callback=self.on_stop),
        ]

    # ── 菜单回调 ─────────────────────────────────────────────
    def on_open_console(self, _):  _open("/")
    def on_open_data(self, _):     _open("/data")
    def on_open_stats(self, _):    _open("/stats/phone")
    def on_open_stats_shareholder(self, _): _open("/stats/shareholder")

    def on_open_data_dir(self, _):
        data_dir = Path(__file__).parent / "data"
        if data_dir.exists():
            subprocess.Popen(["open", str(data_dir)])

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

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
            rumps.MenuItem("一键标注号码", callback=self.on_quick_annotate),
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
            self._notify_result("❌ 读取剪贴板失败", str(e))
            return

        if not text.strip():
            print("[EntHub] 剪贴板为空", flush=True)
            self._notify_result("⚠️ 剪贴板为空", "请先复制包含电话号码的文本")
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
            self._notify_result("❌ 服务未运行", "请先启动 EntHub 服务")
            return
        except Exception as e:
            print(f"[EntHub] API 调用失败: {e}", flush=True)
            self._notify_result("❌ API 调用失败", str(e))
            return

        if data.get("code") != 0:
            msg = data.get("message", "未知错误")
            print(f"[EntHub] 标注失败: {msg}", flush=True)
            self._notify_result("❌ 标注失败", msg)
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
            self._notify_result("❌ 写入剪贴板失败", str(e))
            return

        # 4. 多渠道反馈
        if phone_count == 0:
            self._notify_result(
                "⚠️ 未发现电话号码",
                f"已处理 {len(text)} 字文本"
            )
        else:
            self._notify_result(
                f"✅ 已标注 {phone_count} 个号码",
                "结果已写回剪贴板，可直接粘贴使用"
            )

    def _notify_result(self, title, message):
        """多渠道反馈：系统通知 + 菜单标题短暂变化 + 终端日志"""
        # 1. 终端日志
        print(f"[EntHub] {title} | {message}", flush=True)

        # 2. 系统通知（主反馈）
        try:
            rumps.notification("EntHub 一键标注", title, message)
        except Exception as e:
            print(f"[EntHub] 通知发送失败: {e}", flush=True)

        # 3. 菜单标题短暂变化（备用反馈）
        try:
            original_title = self.title
            # 取标题前几个字作为状态指示
            short_status = title.split()[0] if title else "✓"
            self.title = f"EntHub {short_status}"
            import threading
            def restore():
                import time
                time.sleep(2)
                self.title = original_title
            t = threading.Thread(target=restore, daemon=True)
            t.start()
        except Exception:
            pass

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

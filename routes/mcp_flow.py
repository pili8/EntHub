"""MCP Server 管理页面、启动/停止/状态。"""
import os
import sys
import socket
import subprocess
from pathlib import Path

from flask import Blueprint, render_template, jsonify

bp = Blueprint('mcp_flow_bp', __name__)

MCP_HOST = "localhost"
MCP_PORT = 5310
MCP_URL = f"http://{MCP_HOST}:{MCP_PORT}/mcp"
MCP_PID_FILE = Path(__file__).parent.parent / ".mcp_pid"


def _check_mcp_running():
    """检查 MCP Server 是否在运行"""
    try:
        with socket.create_connection((MCP_HOST, MCP_PORT), timeout=1):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def _read_mcp_pid():
    """读取 MCP 进程 PID"""
    if MCP_PID_FILE.exists():
        try:
            return int(MCP_PID_FILE.read_text().strip())
        except (ValueError, OSError):
            return None
    return None


@bp.route("/mcp")
def mcp_page():
    """MCP 管理页面"""
    running = _check_mcp_running()
    pid = _read_mcp_pid()
    server_url = MCP_URL

    # 本机 IP（用于局域网访问）
    local_ip = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        pass

    lan_url = f"http://{local_ip}:{MCP_PORT}/mcp" if local_ip else None

    # 路径信息（用于 stdio 模式配置）
    project_root = str(Path(__file__).parent.parent.resolve())
    venv_python = str(Path(project_root) / "venv" / "bin" / "python")
    mcp_script = str(Path(project_root) / "mcp_server.py")

    from config import is_password_enabled

    return render_template(
        "mcp.html",
        running=running,
        pid=pid,
        server_url=server_url,
        lan_url=lan_url,
        project_root=project_root,
        venv_python=venv_python,
        mcp_script=mcp_script,
        password_enabled=is_password_enabled(),
    )


@bp.route("/mcp/start", methods=["POST"])
def mcp_start():
    """启动 MCP Server（独立后台进程）"""
    if _check_mcp_running():
        return jsonify({"code": 1001, "message": "MCP Server 已在运行", "data": None})

    venv_python = str(Path(__file__).parent.parent / "venv" / "bin" / "python")
    if not Path(venv_python).exists():
        venv_python = sys.executable

    try:
        # 启动独立进程，stdout/stderr 重定向到日志文件
        log_file = Path(__file__).parent.parent / "mcp_server.log"
        log_fp = open(log_file, "a")
        proc = subprocess.Popen(
            [venv_python,
             str(Path(__file__).parent.parent / "mcp_server.py"), "--http"],
            stdout=log_fp,
            stderr=log_fp,
            cwd=str(Path(__file__).parent.parent),
            start_new_session=True,
        )
        MCP_PID_FILE.write_text(str(proc.pid))

        # 等待最多 3 秒检查是否启动成功
        import time
        for _ in range(30):
            time.sleep(0.1)
            if _check_mcp_running():
                return jsonify({
                    "code": 0,
                    "message": "MCP Server 启动成功",
                    "data": {"pid": proc.pid, "url": MCP_URL}
                })

        # 端口未监听，检查进程是否还活着
        if proc.poll() is None:
            return jsonify({
                "code": 0,
                "message": "MCP Server 进程已启动（端口未就绪，请稍候）",
                "data": {"pid": proc.pid}
            })
        else:
            return jsonify({
                "code": 2001,
                "message": f"MCP Server 启动失败（exit={proc.returncode}），请查看 mcp_server.log",
                "data": None
            })
    except Exception as e:
        return jsonify({"code": 2001, "message": f"启动失败：{e}", "data": None})


@bp.route("/mcp/stop", methods=["POST"])
def mcp_stop():
    """停止 MCP Server"""
    if not _check_mcp_running() and not MCP_PID_FILE.exists():
        return jsonify({"code": 1001, "message": "MCP Server 未在运行", "data": None})

    pid = _read_mcp_pid()
    killed = False

    if pid:
        try:
            os.kill(pid, 15)  # SIGTERM
            killed = True
        except ProcessLookupError:
            pass
        except Exception as e:
            return jsonify({"code": 2001, "message": f"停止失败：{e}", "data": None})

    # 兜底：通过端口找进程
    if not killed and _check_mcp_running():
        try:
            subprocess.run(["lsof", "-ti", f":{MCP_PORT}", "-kill"],
                           check=False, timeout=5)
            killed = True
        except Exception:
            pass

    # 清理 PID 文件
    try:
        MCP_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass

    if killed:
        return jsonify({"code": 0, "message": "MCP Server 已停止", "data": None})
    else:
        return jsonify({"code": 1001, "message": "未找到 MCP Server 进程", "data": None})


@bp.route("/mcp/status")
def mcp_status():
    """查询 MCP Server 状态"""
    return jsonify({
        "code": 0,
        "message": "ok",
        "data": {
            "running": _check_mcp_running(),
            "pid": _read_mcp_pid(),
            "url": MCP_URL,
        }
    })

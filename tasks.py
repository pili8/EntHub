"""跨蓝图共享的后台任务跟踪。

供 import / cleanup 等异步流程使用：记录 queue + stop_event + status。
"""
import queue
import threading

# batch_id -> {"queue": Queue, "stop_event": Event, "status": str}
_tasks = {}


def create(task_id):
    """为给定 ID 创建新任务跟踪，返回 (queue, stop_event)。"""
    q = queue.Queue()
    stop = threading.Event()
    _tasks[task_id] = {"queue": q, "stop_event": stop, "status": "running"}
    return q, stop


def get(task_id):
    """读取任务跟踪信息，不存在返回 None。"""
    return _tasks.get(task_id)


def pop(task_id, default=None):
    """弹出任务跟踪。"""
    return _tasks.pop(task_id, default)


def set_status(task_id, status):
    """更新任务状态。"""
    if task_id in _tasks:
        _tasks[task_id]["status"] = status


def request_stop(task_id):
    """请求停止任务。返回 True 如果找到任务。"""
    task = _tasks.get(task_id)
    if task:
        task["stop_event"].set()
        return True
    return False

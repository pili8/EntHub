"""自动注册 routes/ 下的功能模块。

app.py 入口只需调用 register_blueprints(app)。
每个模块文件顶部声明 MODULE_META（协议见 _base.py），注册器：
1. 先跑一次 init_db()（幂等），确保 settings 表存在、开关可读
2. 扫描 routes/ 下所有 .py（排除 __init__.py / _base.py），import 并读 MODULE_META
3. 无声明的文件跳过（内部工具模块，需手动注册兜底）
4. 核心模块（core=True）无条件注册；可选模块查 settings 开关，停用则跳过
5. 注册动作：挂蓝图 + 收集菜单项 + 调用模块自治建表（tables / init_module）

菜单数据收集在 MODULES（按 group+order 排序，移动端抽屉分组渲染）与
DESKTOP_MODULES（按 desktop_order 排序平铺，桌面顶部导航用）；app.py 的
context_processor 注入为 ENABLED_MODULES / DESKTOP_MODULES，base.html 据此渲染。
"""
import importlib
import sqlite3
from pathlib import Path

from ._base import MODULE_DEFAULTS, MENU_KEYS

_ROUTES_DIR = Path(__file__).resolve().parent
_EXCLUDED = {"__init__.py", "_base.py"}

# 已启用模块的菜单项列表（每项含 name/name_short/icon/url/group/order/show_in）
# 排序规则：group + order —— 移动端抽屉按此分组渲染
MODULES = []
# 桌面顶部导航专用列表：只含 show_in 带 desktop 的项，按 desktop_order（缺省 order）平铺排序
# 桌面是横向按钮条、不分组，顺序需与抽屉不同，故与 MODULES 分开维护
DESKTOP_MODULES = []
# 全部模块元信息（含停用/隐藏的），设置页"模块管理"数据源
ALL_MODULES = []
# 已成功注册路由的模块 id 集合——模板里做 url_for 安全检查用
# （停用模块不在其中，模板引用其端点前应先判断 id 是否在此集合内）
ENABLED_IDS = set()


def _load_meta(module):
    """读取并补全模块的 MODULE_META；无声明返回 None。"""
    meta = getattr(module, "MODULE_META", None)
    if meta is None:
        return None
    m = dict(MODULE_DEFAULTS)
    m.update(meta)
    if not m["id"]:
        m["id"] = module.__name__.rsplit(".", 1)[-1]
    if not m["name"]:
        m["name"] = m["id"]
    if not m["name_short"]:
        m["name_short"] = m["name"]
    return m


def _menu_items(meta):
    """把模块元信息展开成菜单项列表。声明了 menu 的用 menu，否则用顶层单菜单。"""
    if meta.get("menu"):
        items = []
        for it in meta["menu"]:
            item = {k: it.get(k, MODULE_DEFAULTS[k]) for k in MENU_KEYS}
            items.append(item)
        return items
    return [{k: meta[k] for k in MENU_KEYS}]


def _init_tables(meta, module):
    """模块自治建表（tables 声明 + init_module 函数），幂等、失败不阻断启动。"""
    sqls = meta.get("tables") or []
    if sqls:
        try:
            from db import DB_PATH
            conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
            try:
                for sql in sqls:
                    conn.execute(sql)
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print(f"[模块注册] {meta['id']} 建表失败: {e}")
    # tables 之外的复杂初始化（种子数据/触发器等）由模块自带函数完成
    fn = getattr(module, "init_module", None)
    if fn:
        try:
            fn()
        except Exception as e:
            print(f"[模块注册] {meta['id']} init_module 执行失败: {e}")


def register_blueprints(app):
    """扫描 routes/ 自动注册模块蓝图。"""
    try:
        from db import init_db
        init_db()  # 幂等；确保 settings 表存在，开关可读
    except Exception as e:
        print(f"[模块注册] init_db 预执行失败（继续，开关按默认启用）: {e}")

    import config as cfg
    enabled = cfg.get_module_config()

    for f in sorted(_ROUTES_DIR.glob("*.py")):
        if f.name in _EXCLUDED:
            continue
        try:
            module = importlib.import_module(f"routes.{f.stem}")
        except Exception as e:
            print(f"[模块注册] 加载失败 {f.name}: {e}")
            continue

        meta = _load_meta(module)
        if meta is None:
            continue  # 无 MODULE_META：内部工具模块，跳过
        meta["_file"] = f.name  # 真实文件名（删除模块时定位用）

        bp = getattr(module, "bp", None)
        if bp is None:
            print(f"[模块注册] {f.name} 声明了 META 但无 bp，跳过")
            continue

        if not meta["core"]:
            is_on = enabled.get(meta["id"], True)
            deps_ok = all(enabled.get(d, True) for d in meta["depends_on"])
            if not is_on:
                print(f"[模块注册] {meta['id']} 已停用，跳过")
                # 停用钩子：模块可定义 on_disable() 执行清理（如杀进程）
                fn = getattr(module, "on_disable", None)
                if fn:
                    try:
                        fn()
                    except Exception as e:
                        print(f"[模块注册] {meta['id']} on_disable 失败: {e}")
                ALL_MODULES.append(meta)
                continue
            if not deps_ok:
                print(f"[模块注册] {meta['id']} 依赖未启用，跳过")
                ALL_MODULES.append(meta)
                continue

        app.register_blueprint(bp)
        ENABLED_IDS.add(meta["id"])
        # 菜单只收有导航入口的项（show_in 非空）；纯 API 模块仅进 ALL_MODULES
        items = _menu_items(meta)
        MODULES.extend([it for it in items if it["show_in"]])
        DESKTOP_MODULES.extend([it for it in items if "desktop" in it["show_in"]])
        ALL_MODULES.append(meta)
        _init_tables(meta, module)

    MODULES.sort(key=lambda m: (m["group"], m["order"]))
    DESKTOP_MODULES.sort(
        key=lambda m: m["desktop_order"] if m["desktop_order"] is not None else m["order"]
    )

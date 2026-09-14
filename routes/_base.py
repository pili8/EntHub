"""蓝图模块的共享基础：模块元信息协议 + 通用工具。

每个 routes/*.py 模块在顶部声明 MODULE_META 字典，自动注册器（routes/__init__.py）
据此注册蓝图、生成导航、控制启停。无 MODULE_META 的文件视为内部工具模块，
不被自动注册器扫描（需手动注册）。

MODULE_META 字段（均可省略，省略时用 MODULE_DEFAULTS）：
    id         唯一标识，缺省取文件名；写入开关配置，一经发布不要改
    name       菜单显示名（移动端抽屉用长名）
    name_short 桌面导航短名，缺省同 name
    icon       Lucide 图标名
    url        主入口 URL（导航链接）
    group      菜单分组：data / ops / system
    order      组内排序，数字越小越靠前（移动端抽屉按 group + order 渲染）
    desktop_order
               桌面顶部导航专用排序，缺省回退 order。
               桌面导航是平铺的（不分组的横向按钮），需与抽屉顺序不同时用本字段。
    core       True=核心模块：不受开关控制、不可删除
    impact     停用/删除本模块的影响说明（一句白话），设置页「模块管理」每行直接显示。
               写清「关掉会发生什么」，而不只是复述功能名。
    risk       影响等级：high（红，会连带弄坏别的功能或不可逆）/ warn（黄，少一块功能）
               low（灰，只是个入口）。high 的可管理模块，开关会先弹一次确认。
    depends_on 依赖的模块 id 列表；依赖被停用则本模块不注册
    tables     建表 SQL 字符串列表（CREATE TABLE IF NOT EXISTS ...，幂等）
    show_in    导航显示位置：drawer（移动端抽屉）/ desktop（桌面导航）；纯 API 模块给 []
    external   True=被外部系统依赖（Chrome 插件 / MCP），停用时设置页给出提示
    menu       可选：多菜单项列表（一项模块可挂多个导航入口），每项含
               name/name_short/icon/url/group/order/show_in；存在时覆盖顶层单菜单项
"""
from flask import Blueprint

# 各模块统一用 'routes.xxx_bp' 作为蓝图名
def make_bp(name):
    """创建一个 Blueprint。name 是模块简称，如 'pages'。"""
    return Blueprint(f"{name}_bp", __name__)


MODULE_DEFAULTS = {
    "id": "",
    "name": "",
    "name_short": "",
    "icon": "box",
    "url": "",
    "group": "data",
    "order": 100,
    "desktop_order": None,
    "core": False,
    "impact": "",
    "risk": "low",
    "depends_on": [],
    "tables": [],
    "show_in": ["drawer", "desktop"],
    "external": False,
    "menu": None,
}

# 模块菜单项需要的字段（menu 列表项 / 顶层单菜单共用）
MENU_KEYS = ("name", "name_short", "icon", "url", "group", "order",
             "desktop_order", "show_in")

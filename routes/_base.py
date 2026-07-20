"""蓝图模块的共享基础：通用导入和工具。"""
from flask import Blueprint

# 各模块统一用 'routes.xxx_bp' 作为蓝图名
def make_bp(name):
    """创建一个 Blueprint。name 是模块简称，如 'pages'。"""
    return Blueprint(f"{name}_bp", __name__)

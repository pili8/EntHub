#!/bin/bash
# EntHub 一键启动脚本
cd "$(dirname "$0")"

# 检查虚拟环境是否完整
if [ ! -f "venv/bin/activate" ]; then
    echo "正在初始化虚拟环境..."
    rm -rf venv
    python3 -m venv venv
    if [ ! -f "venv/bin/activate" ]; then
        echo "错误：创建虚拟环境失败"
        echo "请确认已安装 Python 3.10+：python3 --version"
        read -p "按回车键关闭窗口..."
        exit 1
    fi
    source venv/bin/activate
    echo "正在安装依赖（首次需要 1~2 分钟）..."
    pip install -r requirements.txt
    echo "初始化完成！"
else
    source venv/bin/activate
fi

# 检测 5210 端口占用
PORT=5210
PIDS=$(lsof -ti :$PORT -sTCP:LISTEN 2>/dev/null)
if [ -n "$PIDS" ]; then
    echo "⚠️  端口 $PORT 已被以下进程占用："
    lsof -i :$PORT -sTCP:LISTEN -nP 2>/dev/null
    echo ""
    read -p "是否终止占用进程并继续启动？(y/N) " confirm
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        echo "正在终止占用进程..."
        kill $PIDS 2>/dev/null
        sleep 1
        PIDS2=$(lsof -ti :$PORT -sTCP:LISTEN 2>/dev/null)
        if [ -n "$PIDS2" ]; then
            echo "进程未响应，强制终止..."
            kill -9 $PIDS2 2>/dev/null
            sleep 1
        fi
        echo "✓ 端口 $PORT 已释放"
    else
        echo "已取消启动。"
        read -p "按回车键关闭窗口..."
        exit 1
    fi
fi

echo "================================"
echo "  EntHub 正在启动"
echo "  本机访问: http://127.0.0.1:5210"
echo "  按 Ctrl+C 退出"
echo "================================"

# 自动打开浏览器（等服务完全启动后再打开）
(sleep 4 && open "http://127.0.0.1:5210" 2>/dev/null) &

python app.py

echo ""
echo "EntHub 已停止运行。"
read -p "按回车键关闭窗口..."

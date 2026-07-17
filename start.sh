#!/bin/bash
# EntHub 启动脚本
# 用法：
#   ./start.sh            前台运行（占用当前终端）
#   ./start.sh --bg       后台运行（用于被 .app 启动器调用，无终端输出）

# 项目根目录绝对路径（为 .app 启动器的 osascript 派发用，不能依赖 cwd）
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

BG_MODE=false
if [ "$1" = "--bg" ] || [ "${ENTHUB_BG:-0}" = "1" ]; then
    BG_MODE=true
fi

# 关键：全部使用绝对路径调 python，避免依赖 PATH 和 activate
# （被 .app 经 osascript 派发时，launchd 给的 PATH 极简，activate 会失效）
VENV_PY="$DIR/venv/bin/python"

# 检查虚拟环境是否完整
if [ ! -x "$VENV_PY" ]; then
    if [ "$BG_MODE" = true ]; then
        osascript -e 'display dialog "EntHub 首次启动需要初始化虚拟环境。\n\n请在终端中手动执行：\n\ncd ~/AI/EntHub && ./start.sh" buttons {"好"} default button "好" with title "EntHub 首次启动" with icon caution' >/dev/null 2>&1
        exit 1
    fi
    echo "正在初始化虚拟环境..."
    rm -rf venv
    python3 -m venv venv
    if [ ! -x "$VENV_PY" ]; then
        echo "错误：创建虚拟环境失败"
        echo "请确认已安装 Python 3.10+：python3 --version"
        read -p "按回车键关闭窗口..."
        exit 1
    fi
    "$VENV_PY" -m pip install -r requirements.txt
    echo "初始化完成！"
fi

# 自愈：venv 存在但关键依赖缺失 → 补装（不重装虚拟环境本身）
if ! "$VENV_PY" -c "import flask, pandas, openpyxl, rumps" 2>/dev/null; then
    if [ "$BG_MODE" = true ]; then
        osascript -e 'display dialog "EntHub 虚拟环境依赖不完整，请先在终端跑一次 ./start.sh 补装依赖。" buttons {"好"} default button "好" with title "EntHub 需要补装依赖" with icon caution' >/dev/null 2>&1
        exit 1
    fi
    echo "检测到虚拟环境依赖不完整，正在补装..."
    "$VENV_PY" -m pip install -r requirements.txt
    echo "补装完成！"
fi

# 检测 5210 端口占用
PORT=5210
PIDS=$(lsof -ti :$PORT -sTCP:LISTEN 2>/dev/null)
if [ -n "$PIDS" ]; then
    if [ "$BG_MODE" = true ]; then
        # 后台模式：检查是否已经有 EntHub 在跑；如果是，直接打开浏览器即可
        PROCESS_INFO=$(ps -p $PIDS -o command= 2>/dev/null | head -1)
        if echo "$PROCESS_INFO" | grep -q "app.py"; then
            # 已经在跑：直接打开浏览器
            open "http://127.0.0.1:$PORT" 2>/dev/null
            exit 0
        else
            # 端口被别的进程占用：弹原生 dialog
            osascript -e "display dialog \"端口 $PORT 已被其他进程占用：\n\n$PROCESS_INFO\n\n请先关闭占用进程后再启动 EntHub。\" buttons {\"好\"} default button \"好\" with title \"EntHub 启动失败\" with icon stop" >/dev/null 2>&1
            exit 1
        fi
    fi
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

# ─────────── 后台模式 ───────────
if [ "$BG_MODE" = true ]; then
    LOG_FILE="$HOME/Library/Logs/EntHub.log"
    PID_FILE="$HOME/Library/Application Support/EntHub/enthub.pid"
    MENUBAR_PID_FILE="$HOME/Library/Application Support/EntHub/enthub-menubar.pid"
    mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$PID_FILE")" "$(dirname "$MENUBAR_PID_FILE")"

    # 启动 Flask 到后台（用绝对路径调 venv python）
    nohup "$VENV_PY" "$DIR/app.py" >> "$LOG_FILE" 2>&1 &
    APP_PID=$!
    echo "$APP_PID" > "$PID_FILE"

    # 等 Flask 起来（最多 10 秒）
    for i in {1..20}; do
        sleep 0.5
        if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT" 2>/dev/null | grep -q "200\|301\|302"; then
            # 启动成功 → 启动状态栏菜单（仅 macOS）→ 打开浏览器
            if [ "$(uname -s)" = "Darwin" ] && [ -f "$DIR/menubar.py" ]; then
                nohup "$VENV_PY" "$DIR/menubar.py" >> "$LOG_FILE" 2>&1 &
                MENUBAR_PID=$!
                echo "$MENUBAR_PID" > "$MENUBAR_PID_FILE"
                # 写一行启动日志，确认 menubar 派发了
                echo "$(date '+%Y-%m-%d %H:%M:%S') menubar launched (pid=$MENUBAR_PID)" >> "$LOG_FILE"
            fi
            open "http://127.0.0.1:$PORT" 2>/dev/null
            exit 0
        fi
        # 检查进程是否还在
        if ! kill -0 "$APP_PID" 2>/dev/null; then
            osascript -e "display dialog \"EntHub 启动失败。\n\n请查看日志：\n$LOG_FILE\" buttons {\"好\"} default button \"好\" with title \"EntHub 启动失败\" with icon stop" >/dev/null 2>&1
            exit 1
        fi
    done

    # 超时
    osascript -e "display dialog \"EntHub 启动超时（10 秒内未响应）。\n\n请查看日志：\n$LOG_FILE\" buttons {\"好\"} default button \"好\" with title \"EntHub 启动失败\" with icon stop" >/dev/null 2>&1
    exit 1
fi

# ─────────── 前台模式（保留原行为） ───────────
echo "================================"
echo "  EntHub 正在启动"
echo "  本机访问: http://127.0.0.1:5210"
echo "  按 Ctrl+C 退出"
echo "================================"

# 自动打开浏览器（等服务完全启动后再打开）
(sleep 4 && open "http://127.0.0.1:5210" 2>/dev/null) &

"$VENV_PY" app.py

echo ""
echo "EntHub 已停止运行。"
read -p "按回车键关闭窗口..."

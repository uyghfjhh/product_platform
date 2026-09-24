#!/usr/bin/env bash
# ==============================================================================
# product_platform 独立 Web 平台服务后台守护进程管理脚本
#
# 支持命令:
#   ./web.sh start [port] [host]  # 后台启动 Web 服务 (默认端口: 8080, 主机: 0.0.0.0)
#   ./web.sh stop                 # 优雅停止 Web 服务及后台任务
#   ./web.sh status               # 查看当前 Web 服务运行状态及访问地址
#   ./web.sh restart [port]       # 平滑重启服务
#   ./web.sh logs                 # 实时查看 Web 与任务执行日志
# ==============================================================================
set -euo pipefail

# 确定平台根目录与数据目录
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${PRODUCT_PLATFORM_DATA_DIR:-$ROOT_DIR/data}"
mkdir -p "$DATA_DIR"

PID_FILE="$DATA_DIR/web.pid"
PORT_FILE="$DATA_DIR/web.port"
LOG_FILE="$DATA_DIR/web.log"

DEFAULT_PORT=8080
DEFAULT_HOST="0.0.0.0"

# 优先选择本地虚拟环境中的 Python，或依次检测 Python 3.12 (uv/pyenv/系统路径) 及后备版本
find_python_bin() {
    if [ -f "$ROOT_DIR/.venv/bin/python3" ]; then
        echo "$ROOT_DIR/.venv/bin/python3"
        return 0
    fi
    for candidate in \
        python3.12 \
        "$HOME/.local/share/uv/python/cpython-3.12"*/bin/python3 \
        "$HOME/.pyenv/versions/3.12"*/bin/python3 \
        /usr/local/bin/python3.12 \
        python3.11 \
        python3.10 \
        python3.9 \
        python3.8 \
        python3; do
        if [ -x "$candidate" ] 2>/dev/null; then
            echo "$candidate"
            return 0
        fi
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(find_python_bin || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "错误: 未检测到 Python 解释器，请先执行 ./web.sh setup 安装运行环境" >&2
    exit 1
fi

# 检查指定 PID 是否属于当前平台服务进程
is_platform_process() {
    local pid="$1"
    if ! kill -0 "$pid" 2>/dev/null; then
        return 1
    fi
    if [ -f "/proc/$pid/cmdline" ]; then
        if tr '\0' ' ' < "/proc/$pid/cmdline" | grep -qE "platform_app\.cli|product-platform"; then
            return 0
        fi
    fi
    return 0
}

# 获取当前正在运行的 Web 服务 PID
get_running_pid() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid="$(cat "$PID_FILE" 2>/dev/null || true)"
        if [ -n "$pid" ] && is_platform_process "$pid"; then
            echo "$pid"
            return 0
        fi
        rm -f "$PID_FILE" "$PORT_FILE"
    fi

    # 兜底探测: 检查是否有 platform_app.cli start 正在运行
    local found_pid
    found_pid="$(pgrep -f "platform_app\.cli.*start|product-platform.*start" 2>/dev/null | grep -v "$$" | head -n 1 || true)"
    if [ -n "$found_pid" ]; then
        echo "$found_pid" > "$PID_FILE"
        echo "$found_pid"
        return 0
    fi

    return 1
}

# 启动 Web 平台服务
do_start() {
    local port="$DEFAULT_PORT"
    local host="$DEFAULT_HOST"

    # 解析命令行参数 (如: ./web.sh start 8080 或 ./web.sh start --port 8080)
    while [ $# -gt 0 ]; do
        case "$1" in
            --port|-p)
                port="$2"
                shift 2
                ;;
            --host|-h)
                host="$2"
                shift 2
                ;;
            [0-9]*)
                port="$1"
                shift
                ;;
            *)
                shift
                ;;
        esac
    done

    local pid
    if pid="$(get_running_pid)"; then
        local current_port
        current_port="$(cat "$PORT_FILE" 2>/dev/null || echo "$port")"
        echo "⚠️  平台 Web 服务已经在后台运行中 (PID: $pid)"
        echo "   本地访问地址: http://127.0.0.1:$current_port"
        echo "   停止命令:     ./web.sh stop"
        return 0
    fi

    # 检查当前 Python 解释器是否包含必需的 Web 服务依赖 (uvicorn, fastapi, psycopg)
    if ! "$PYTHON_BIN" -c "import uvicorn, fastapi, psycopg" >/dev/null 2>&1; then
        echo "⚠️  检测到当前运行环境依赖不完整 (如缺少 uvicorn / fastapi / psycopg 等)..."
        if [ -f "$ROOT_DIR/install_env.sh" ]; then
            echo "🚀 正在自动执行 ./install_env.sh 补全环境并安装依赖..."
            bash "$ROOT_DIR/install_env.sh"
            PYTHON_BIN="$(find_python_bin || echo "$ROOT_DIR/.venv/bin/python3")"
        else
            echo "❌ 启动失败: 请先执行 ./install_env.sh 安装环境依赖！"
            return 1
        fi
    fi

    echo "正在后台启动平台 Web 服务 (端口: $port, 主机: $host)..."

    # 设置 PYTHONPATH 确保可以加载 backend 目录模块
    export PYTHONPATH="$ROOT_DIR/backend:${PYTHONPATH:-}"

    # 后台守护进程启动: 使用 setsid 彻底脱离控制终端与父进程会话
    if command -v setsid >/dev/null 2>&1; then
        setsid "$PYTHON_BIN" -m platform_app.cli start --host "$host" --port "$port" >> "$LOG_FILE" 2>&1 &
    else
        nohup "$PYTHON_BIN" -m platform_app.cli start --host "$host" --port "$port" >> "$LOG_FILE" 2>&1 &
    fi
    local new_pid=$!
    disown $new_pid 2>/dev/null || true

    echo "$new_pid" > "$PID_FILE"
    echo "$port" > "$PORT_FILE"

    # 等待探测服务端口启动就绪 (最多等待 5 秒)
    local started=false
    for _ in $(seq 1 10); do
        sleep 0.5
        if ! kill -0 "$new_pid" 2>/dev/null; then
            break
        fi
        if command -v ss >/dev/null 2>&1; then
            if ss -tlnp 2>/dev/null | grep -q ":$port "; then
                started=true
                break
            fi
        fi
    done

    if kill -0 "$new_pid" 2>/dev/null; then
        echo "✅ 平台 Web 服务已成功在后台启动 (PID: $new_pid)"
        echo "   本地访问地址: http://127.0.0.1:$port"
        echo "   局域网地址:   http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo "0.0.0.0"):$port"
        echo "   日志文件:     $LOG_FILE"
    else
        echo "❌ 启动失败，请检查运行日志: $LOG_FILE"
        tail -n 20 "$LOG_FILE" 2>/dev/null || true
        rm -f "$PID_FILE" "$PORT_FILE"
        return 1
    fi
}

# 停止 Web 平台服务
do_stop() {
    local pid
    if ! pid="$(get_running_pid)"; then
        echo "ℹ️  当前没有正在运行的平台 Web 服务。"
        rm -f "$PID_FILE" "$PORT_FILE"
        return 0
    fi

    echo "正在停止平台 Web 服务 (PID: $pid)..."

    # 首先发送 SIGTERM 请求正常退出
    kill -TERM "$pid" 2>/dev/null || true

    # 等待进程平稳退出 (最多 5 秒)
    local stopped=false
    for _ in $(seq 1 10); do
        if ! kill -0 "$pid" 2>/dev/null; then
            stopped=true
            break
        fi
        sleep 0.5
    done

    # 若进程未在超时时间内退出，则强制 SIGKILL
    if [ "$stopped" = false ] && kill -0 "$pid" 2>/dev/null; then
        echo "⚠️  服务未在预期内停止，正在发送强制终止信号 (SIGKILL)..."
        kill -9 "$pid" 2>/dev/null || true
        sleep 0.5
    fi

    rm -f "$PID_FILE" "$PORT_FILE"
    echo "✅ 平台 Web 服务已完全停止。"
}

# 查看 Web 平台服务状态
do_status() {
    local pid
    if pid="$(get_running_pid)"; then
        local port
        port="$(cat "$PORT_FILE" 2>/dev/null || echo "$DEFAULT_PORT")"
        echo "🟢 状态: 运行中"
        echo "   PID:          $pid"
        echo "   本地访问地址: http://127.0.0.1:$port"
        echo "   局域网地址:   http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo "0.0.0.0"):$port"
        echo "   日志路径:     $LOG_FILE"
    else
        echo "⚪ 状态: 未运行"
        echo "   启动命令: ./web.sh start"
    fi
}

# 跟踪查看运行日志
do_logs() {
    if [ ! -f "$LOG_FILE" ]; then
        echo "ℹ️  日志文件尚不存在: $LOG_FILE"
        return 0
    fi
    echo "正在跟踪服务日志 (Ctrl+C 退出)..."
    tail -f -n 50 "$LOG_FILE"
}

# 一键初始化虚拟环境并安装平台依赖
do_setup() {
    if [ -f "$ROOT_DIR/install_env.sh" ]; then
        bash "$ROOT_DIR/install_env.sh" "$@"
    else
        echo "❌ 错误: 未找到 $ROOT_DIR/install_env.sh 脚本" >&2
        return 1
    fi
}

# 脚本命令行分支路由
COMMAND="${1:-status}"
shift || true

case "$COMMAND" in
    start)
        do_start "$@"
        ;;
    stop)
        do_stop
        ;;
    restart)
        do_stop
        sleep 1
        do_start "$@"
        ;;
    status)
        do_status
        ;;
    logs|log)
        do_logs
        ;;
    setup|init)
        do_setup "$@"
        ;;
    help|--help|-h)
        echo "用法: ./web.sh {start|stop|restart|status|logs|setup} [port] [host]"
        echo "  start [port] [host]  启动后台 Web 服务 (默认端口: 8080)"
        echo "  stop                 停止服务"
        echo "  restart [port]       重启服务"
        echo "  status               查看状态"
        echo "  logs                 跟踪日志"
        echo "  setup                一键初始化 Python 虚拟环境并安装所需依赖"
        ;;
    *)
        echo "未知命令: $COMMAND"
        echo "用法: ./web.sh {start|stop|restart|status|logs|setup} [port] [host]"
        exit 1
        ;;
esac


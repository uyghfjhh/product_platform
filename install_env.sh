#!/usr/bin/env bash
# ==============================================================================
# product_platform 运行环境全自动一键配置脚本 (install_env.sh)
#
# 特性：
# 1. 纯用户态安装 (免 root / sudo)，100% 隔离，绝不影响正在运行的数据库与测试环境
# 2. 自动检测并就绪 Astral uv 极速包管理器
# 3. 自动安装官方独立版 Python 3.12
# 4. 创建 .venv 虚拟环境并安装全部核心依赖 (fastapi, uvicorn, pydantic, psycopg 等)
# 5. 全面自检测试模块导入与 FastAPI 应用装载
# ==============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

echo "=================================================================="
echo "🚀 开始为 product_platform 初始化运行环境 (Python 3.12 + 依赖库)..."
echo "=================================================================="

# 1. 检测或自动安装 uv (Astral 纯二进制包管理器，无任何系统依赖)
UV_BIN=""
for candidate in uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
    if command -v "$candidate" >/dev/null 2>&1; then
        UV_BIN="$(command -v "$candidate")"
        break
    elif [ -x "$candidate" ]; then
        UV_BIN="$candidate"
        break
    fi
done

if [ -z "$UV_BIN" ]; then
    echo "📦 未检测到 uv 工具，正在一键安装至用户目录 (~/.local/bin)..."
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        echo "❌ 错误: 系统缺少 curl 或 wget，无法自动下载 uv。" >&2
        exit 1
    fi
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    UV_BIN="$HOME/.local/bin/uv"
fi

if [ ! -x "$UV_BIN" ]; then
    echo "❌ 错误: uv 安装未成功，请检查网络。" >&2
    exit 1
fi
echo "✅ uv 工具就绪: $($UV_BIN --version)"

# 2. 确保已安装 Python 3.12 独立包
echo "🔍 检查 Python 3.12 解释器..."
BASE_PYTHON=""

# 优先查找 uv 管理的 3.12
for candidate in \
    "$HOME/.local/share/uv/python/cpython-3.12"*/bin/python3 \
    python3.12 \
    /usr/local/bin/python3.12 \
    "$HOME/.pyenv/versions/3.12"*/bin/python3; do
    if [ -x "$candidate" ] 2>/dev/null; then
        BASE_PYTHON="$candidate"
        break
    elif command -v "$candidate" >/dev/null 2>&1; then
        BASE_PYTHON="$(command -v "$candidate")"
        break
    fi
done

if [ -z "$BASE_PYTHON" ]; then
    echo "⬇️  正在通过 uv 下载官方独立版 Python 3.12 (耗时约数秒，仅存放于用户目录)..."
    "$UV_BIN" python install 3.12
    for candidate in "$HOME/.local/share/uv/python/cpython-3.12"*/bin/python3; do
        if [ -x "$candidate" ] 2>/dev/null; then
            BASE_PYTHON="$candidate"
            break
        fi
    done
fi

if [ -z "$BASE_PYTHON" ] || [ ! -x "$BASE_PYTHON" ]; then
    echo "❌ 错误: 未能就绪 Python 3.12 解释器！" >&2
    exit 1
fi
echo "✅ Python 3.12 解释器就绪: $BASE_PYTHON ($("$BASE_PYTHON" --version))"

# 3. 创建或更新专属虚拟环境 .venv
VENV_DIR="$ROOT_DIR/.venv"
echo "🔨 正在构建虚拟环境: $VENV_DIR ..."

# 如果旧虚拟环境不是 3.12，进行安全清理
if [ -d "$VENV_DIR" ]; then
    CURRENT_VENV_VER="$("$VENV_DIR/bin/python3" --version 2>/dev/null || echo "unknown")"
    if ! echo "$CURRENT_VENV_VER" | grep -q "3\.12"; then
        echo "ℹ️  旧虚拟环境版本 ($CURRENT_VENV_VER) 非 Python 3.12，正在平滑重建..."
        rm -rf "$VENV_DIR"
    fi
fi

if [ ! -f "$VENV_DIR/bin/python3" ]; then
    "$UV_BIN" venv --python "$BASE_PYTHON" "$VENV_DIR"
fi

# 4. 安装/同步平台依赖库
echo "📦 正在安装平台依赖库 (requirements.txt: fastapi, uvicorn, psycopg, pydantic...)..."
"$UV_BIN" pip install --python "$VENV_DIR/bin/python3" -r "$ROOT_DIR/requirements.txt"

# 5. 严格依赖自检与模块导入测试
echo "🧪 正在执行依赖自检与核心应用装载验证..."
"$VENV_DIR/bin/python3" -c "
import fastapi
import uvicorn
import pydantic
import yaml
import huey
import psycopg
import cryptography
import argon2
import nacl
import jinja2
print('   -> [OK] 外部依赖库全部导入成功')
"

PYTHONPATH="$ROOT_DIR/backend" "$VENV_DIR/bin/python3" -c "
from platform_app.api import app
print('   -> [OK] platform_app 后端 API 应用正常装载成功')
"

echo "=================================================================="
echo "🎉 product_platform 运行环境已 100% 配置完成！"
echo "   后续您只需要执行以下命令即可启动 Web 平台服务："
echo "   ./web.sh start"
echo "=================================================================="

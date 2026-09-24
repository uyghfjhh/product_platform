#!/usr/bin/env bash
# ==============================================================================
# fbasecman 回归测试系统本地执行入口 (regress/fbasecman/run.sh)
#
# 常用命令:
#   ./run.sh env setup          # 基于 pgcluster 自动部署 14 节点集群并初始化测试夹具
#   ./run.sh env status         # 查看集群节点拓扑与健康状态
#   ./run.sh env start/stop     # 启动/停止集群
#   ./run.sh env restart        # 重启集群
#   ./run.sh env clean          # 清理集群数据目录与残留进程
#   ./run.sh run <target>       # 运行测试用例或套件 (如: rw_toggle, guc.sample)
#   ./run.sh run failed         # 重新运行上次失败的测试用例
#   ./run.sh doctor             # 检查前置依赖与工具环境
#   ./run.sh clean --output     # 清理测试产物文件
#   ./run.sh show <target>      # 查看测试套件用例清单
#   ./run.sh test               # 执行 269 项框架单元测试
# ==============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 优先选择本地虚拟环境中的 Python，或依次检测 python3.12/3.11/3.10/3.9/3.8
PYTHON_BIN=""
if [ -f "$ROOT_DIR/../../.venv/bin/python3" ]; then
    PYTHON_BIN="$ROOT_DIR/../../.venv/bin/python3"
elif [ -f "$ROOT_DIR/.venv/bin/python3" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python3"
else
    for candidate in python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PYTHON_BIN="$(command -v "$candidate")"
            break
        fi
    done
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "错误: 未检测到系统 Python 解释器" >&2
    exit 1
fi

export PYTHONPATH="$ROOT_DIR:$ROOT_DIR/../../backend:$ROOT_DIR/../..:${PYTHONPATH:-}"
exec "$PYTHON_BIN" "$ROOT_DIR/tools/cli.py" "$@"

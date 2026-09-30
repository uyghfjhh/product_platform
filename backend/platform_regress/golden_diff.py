"""回归报告 golden-diff：规范化动态字段后逐字对比报告语义。

定位是验收门而非通用 diff——改写 executor/runtime 后，新报告与基线
在步骤结构、标题、预期、实际值、判定上必须逐字一致，只允许白名单内的
运行时非确定性字段（时间戳、端口、连接 ID、日志 token、µs 计时、
monitor 探测计数器等）豁免。

用法::

    # 单对文件
    python -m platform_regress.golden_diff golden.txt actual.txt

    # 目录对目录（按文件名配对 *.report.txt / report.txt）
    python -m platform_regress.golden_diff /tmp/golden output/regression/cman-mmr/.../

    # 采集基线：把产物目录的报告复制成 <target>.report.txt 命名的 golden 目录
    python -m platform_regress.golden_diff --capture output/regression/cman-mmr /tmp/golden/cman-mmr

退出码：0 全部一致；1 存在差异或缺失；2 用法错误。
"""

from __future__ import annotations

import difflib
import re
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 规范化规则
#
# 每条规则只豁免"同一语义前提下运行时必然抖动"的字段。新增规则必须满足：
#   1. 该字段不同次运行取不同值（非代码语义决定）；
#   2. 报告断言不依赖该字段的具体取值；
#   3. 替换范围尽量收窄（先限定到证据行/日志行再替换数字）。
# ---------------------------------------------------------------------------

_TS = re.compile(r'20\d\d[-/]\d\d[-/]\d\d[ T]\d\d:\d\d:\d\d(?:\.\d+)?\s*(?:[+-]\d\d:?\d\d)?')
# fbasecman/PG 日志行内的会话 token：[ic<hex> ms<hex>]、[c<hex> none]、[ic<hex> none]
_LOG_TOKEN = re.compile(r'\[(?:i?c|ms)?[0-9a-f]{6,}\s+(?:ms[0-9a-f]{6,}|none)\]')
_HEX32 = re.compile(r'\b[0-9a-f]{32}\b')          # 执行/artifact ID
_PID = re.compile(r'\b(pid|PID)[ :=]\s*\d+\b')
_USEC = re.compile(r'\d+\s*u(?:sec|s|c)\b')       # 2982us / 88 usec
_WAIT = re.compile(r'等待 \d+(?:\.\d+)? 秒')
_ID_EQ = re.compile(r'(id=)\d+')
_ERRNO = re.compile(r'error:\s*\d+\b')
# 4-5 位裸数字按端口处理：行尾、空白、逗号、冒号、右括号/管道符边界
_PORT = re.compile(r'\b\d{4,5}\b(?=[\s:,)\]|]|$)')
# monitor/SHOW 证据行内的计数器单元格（仅对已含 <TS> 的行生效，见 normalize）
_COUNTER_CELL = re.compile(r'\|\s*\d+\s*(?=\|)')


def normalize(text: str) -> str:
    """把报告中的运行时动态字段替换为占位符，返回规范化文本。"""
    text = _TS.sub('<TS>', text)
    text = _LOG_TOKEN.sub('[<TOK>]', text)
    text = _HEX32.sub('<HEX>', text)
    text = _PID.sub(r'\1=<N>', text)
    text = _USEC.sub('<N>us', text)
    text = _WAIT.sub('等待 <N> 秒', text)
    text = _ID_EQ.sub(r'\1<N>', text)
    text = _ERRNO.sub('error:<N>', text)
    text = _PORT.sub('<PORT>', text)
    text = re.sub(r'connection ID[^\n]*', '<CID>', text)
    # 证据/监控表行：带时间戳的行里裸数字单元格是探测/重试计数，随窗口抖动
    return '\n'.join(
        _COUNTER_CELL.sub('| <N> ', line) if '<TS>' in line else line
        for line in text.split('\n')
    )


# ---------------------------------------------------------------------------
# 对比与采集
# ---------------------------------------------------------------------------

def diff_text(golden: str, actual: str, golden_name: str, actual_name: str,
              context: int = 3) -> str:
    """规范化后生成 unified diff；无差异返回空串。"""
    g, a = normalize(golden), normalize(actual)
    if g == a:
        return ''
    return ''.join(difflib.unified_diff(
        g.splitlines(keepends=True), a.splitlines(keepends=True),
        fromfile=golden_name, tofile=actual_name, n=context,
    ))


def _case_key(path: Path) -> str:
    """报告文件 → 用例键。

    - ``<suite>.<target>.report.txt`` / ``<target>.report.txt`` → 文件名主干
    - ``<suite>.<target>/report.txt`` → 用例目录名
    - ``<suite>.<target>/artifacts/<exec>/report.txt`` → artifacts 上级目录名
    """
    if path.name not in ('report.txt', 'legacy-report.txt'):
        return path.name.removesuffix('.report.txt')
    parent = path.parent
    if parent.parent.name == 'artifacts':
        parent = parent.parent.parent
    key = parent.name
    return f'{key}.legacy' if path.name == 'legacy-report.txt' else key


def _report_files(root: Path) -> dict[str, Path]:
    """收集目录下报告文件，键为用例键；同键多份取 mtime 最新（同用例多次执行）。

    ``<target>/report.txt`` 是平台写的结论摘要存根，完整报告在
    ``artifacts/<exec>/report.txt``——裸 report.txt 仅当其位于 artifacts 树下
    才计入；golden 侧统一用 ``<target>.report.txt`` 命名。
    """
    files: dict[str, Path] = {}
    for p in sorted(root.rglob('*.txt')):
        if not p.is_file():
            continue
        named = p.name.endswith('.report.txt')
        bare = p.name in ('report.txt', 'legacy-report.txt')
        if named and bare:
            continue  # 如 report.report.txt 之类异常名
        if not named and not bare:
            continue
        if bare and 'artifacts' not in p.parts:
            continue  # 目标级摘要存根，非完整报告
        key = _case_key(p)
        if key not in files or p.stat().st_mtime > files[key].stat().st_mtime:
            files[key] = p
    return files


def _match_keys(golden_keys: list[str], actual_keys: list[str]) -> list[tuple[str | None, str | None]]:
    """按"相同或点分后缀"配对两侧用例键；未配对键原样保留。"""
    pairs: list[tuple[str | None, str | None]] = []
    remaining = list(actual_keys)
    for g in sorted(golden_keys):
        hit = next((a for a in remaining if a == g or a.endswith('.' + g) or g.endswith('.' + a)), None)
        if hit is not None:
            remaining.remove(hit)
            pairs.append((g, hit))
        else:
            pairs.append((g, None))
    pairs.extend((None, a) for a in sorted(remaining))
    return pairs


def compare_paths(golden: Path, actual: Path, out=sys.stdout) -> int:
    """对比单文件或两个目录。返回差异文件数。"""
    if golden.is_file() and actual.is_file():
        pairs = [(golden.name, golden, actual)]
    elif golden.is_dir() and actual.is_dir():
        g_map, a_map = _report_files(golden), _report_files(actual)
        pairs = [(gk or ak,
                  g_map.get(gk) if gk else None,
                  a_map.get(ak) if ak else None)
                 for gk, ak in _match_keys(list(g_map), list(a_map))]
    else:
        print(f'路径形态不一致: {golden} vs {actual}', file=sys.stderr)
        return -1

    differ = 0
    for label, g, a in pairs:
        if g is None:
            print(f'== {label}: 基线缺失（新产物）')
            differ += 1
            continue
        if a is None:
            print(f'== {label}: 产物缺失（基线存在但未产出）')
            differ += 1
            continue
        text = diff_text(g.read_text(encoding='utf-8', errors='replace'),
                         a.read_text(encoding='utf-8', errors='replace'),
                         str(g), str(a))
        if text:
            print(f'== {label}: DIFFERS')
            print(text)
            differ += 1
        else:
            print(f'== {label}: IDENTICAL')
    return differ


def capture(source_root: Path, dest_root: Path) -> int:
    """采集基线：每份 report.txt 以所属用例目录名命名存入 dest_root。

    布局约定：``<target>/artifacts/<exec_id>/report.txt`` → 目标目录名即
    ``artifacts`` 上级的目录名。``<target>/report.txt`` 是结论摘要存根不采集。
    同一用例多次执行的产物会互相覆盖，需指定单次执行目录作基线来源。
    """
    dest_root.mkdir(parents=True, exist_ok=True)
    count = 0
    reports = [p for name in ('report.txt', 'legacy-report.txt')
               for p in source_root.rglob(name)]
    for report in sorted(reports):
        if 'artifacts' not in report.parts:
            continue  # 摘要存根
        rel = report.parent
        parts = rel.parts
        case_name = parts[parts.index('artifacts') - 1] if 'artifacts' in parts else rel.name
        suffix = '.legacy' if report.name == 'legacy-report.txt' else ''
        dest = dest_root / f'{case_name}{suffix}.report.txt'
        shutil.copy2(report, dest)
        count += 1
        print(f'capture {report} -> {dest}')
    return count


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) >= 3 and args[0] == '--capture':
        count = capture(Path(args[1]), Path(args[2]))
        print(f'共采集 {count} 份基线')
        return 0 if count else 2
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    result = compare_paths(Path(args[0]), Path(args[1]))
    if result < 0:
        return 2
    print(f'\n汇总: {"全部一致" if result == 0 else f"{result} 个文件存在差异"}')
    return 0 if result == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())

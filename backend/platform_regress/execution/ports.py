"""非临时端口段的空闲端口探测与连续端口块分配。

回归用例需要为代理监听端口动态选口：固定端口易被历史残留进程占用，
而内核临时端口段（ephemeral range）又可能被客户端连接抢占。
``free_port_block`` 统一提供避开临时段的 N 连端口分配，
具体需要几个端口由各产品调用方决定。
"""

import os
import socket
from pathlib import Path


def port_is_free(port):
    """探测本地 TCP 端口是否可绑定；无权限探测时保守返回 True。"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except PermissionError:
        # The hosted runner may prohibit even probe socket creation.  The
        # product startup remains authoritative for actual bind conflicts.
        return True
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", int(port)))
        return True
    except PermissionError:
        # The hosted runner may forbid a probe bind while still allowing the
        # product process to bind its configured listener.
        return True
    except OSError:
        return False
    finally:
        sock.close()


def non_ephemeral_port_range():
    """返回内核临时端口段之外最宽的可用区间 (low, high)。"""
    low, high = 32768, 60999
    try:
        values = Path("/proc/sys/net/ipv4/ip_local_port_range").read_text().split()
        low, high = int(values[0]), int(values[1])
    except (OSError, ValueError, IndexError):
        pass
    ranges = [(1024, low - 1), (high + 1, 65532)]
    usable = [(start, end) for start, end in ranges if end - start >= 2]
    if not usable:
        raise RuntimeError("no non-ephemeral TCP port range available")
    return max(usable, key=lambda item: item[1] - item[0])


def free_port_block(seed, count, is_free=None, port_range=None):
    """在非临时段内分配 count 个连续空闲端口，返回起始端口。

    seed 叠加进程号做起点散列，降低并发用例撞到同一候选端口的概率。
    ``is_free``/``port_range`` 可注入替换实现，便于测试桩或兼容旧调用方。
    """
    is_free = is_free or port_is_free
    range_start, range_end = (port_range or non_ephemeral_port_range)()
    slots = (range_end - range_start - count + 2) // count
    start_index = (os.getpid() * 17 + seed) % slots
    for offset in range(slots):
        base = range_start + count * ((start_index + offset) % slots)
        if all(is_free(base + index) for index in range(count)):
            return base
    raise RuntimeError("no free port block available")

"""Run upstream 7x24 workloads with the platform-managed fbasecman proxy."""

import argparse
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from platform_regress.clients.pgbench import parse_pgbench_result
from platform_regress.execution.locking import ExclusiveFileLock

TARGET = "upstream-7x24"
DEFAULT_SECONDS = 600
WORKLOADS = {
    "pgbench1": "MMR Hint 长连接读写切换",
    "pgbench2": "MMR Hint 短连接读写切换",
    "pgbench3": "MMR 读端口",
    "pgbench4": "MMR 写端口",
    "pgbench5": "流复制 Hint 读写切换",
    "pgbench6": "Balance 负载均衡",
    "pgbench7": "控制台节点状态查询",
    "pgbench8": "第二事务用户读写模式切换",
    "pgbench9": "SQL Parse 读写路由",
    "pgbench10": "持续 RELOAD",
    "jdbc": "循环 JDBC 路由测试",
    "session_monitor": "独立 JDBC 会话巡检",
}


def suite_directory(source_root):
    if source_root is None:
        raise ValueError("未配置 fbasecman 源码目录")
    suite = source_root / "test" / "7x24小时长稳测试"
    for asset in ("test.conf.bak", "userlist.conf", "set_stabilize.sh", "create_procdure.sql",
                  "contrib/pg_cron-main.zip", "pgbench/call_testbench.sh.bak", "JDBC/config.properties",
                  "JDBC/session_monitor.properties", "JDBC/test_Jdbc.sh",
                  "JDBC/test_Jdbc.java", "JDBC/SessionMonitor.java",
                  *(f"pgbench/test{i}.sql" for i in range(1, 11))):
        if not (suite / asset).is_file():
            raise ValueError(f"fbasecman 源码缺少常稳资产 {asset}")
    return suite


def selected_workloads(parameters):
    selection = parameters.get("enabled_workloads", list(WORKLOADS))
    duration = parameters.get("duration_seconds", DEFAULT_SECONDS)
    if not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= 2592000:
        raise ValueError("常稳时长须为 1 至 2592000 秒")
    if (not isinstance(selection, list) or not selection
            or any(not isinstance(name, str) or name not in WORKLOADS for name in selection)
            or len(set(selection)) != len(selection)):
        raise ValueError("请选择有效且不重复的 7×24 常稳项目")
    return duration, selection


def pgbench_commands(suite, duration, selected, main_port, write_port):
    commands = {}
    template = suite / "pgbench/call_testbench.sh.bak"
    for line in template.read_text(encoding="utf-8").splitlines():
        if not line.startswith("start_pgbench "):
            continue
        _, name, *argv = shlex.split(line.partition(" || ")[0])
        if name not in selected:
            continue
        try:
            if argv[argv.index("-f") + 1] != f"test{name[7:]}.sql":
                raise ValueError("SQL 脚本名称不匹配")
            port = {"FBASECMAN_PORT": main_port, "WRITE_PORT": write_port}[argv[argv.index("-p") + 1]]
            argv[argv.index("-h") + 1] = "127.0.0.1"
            argv[argv.index("-p") + 1] = str(port)
            argv[argv.index("-T") + 1] = str(duration)
        except (KeyError, ValueError, IndexError) as exc:
            raise ValueError(f"{name} 的上游命令格式不匹配") from exc
        commands[name] = argv
    if set(commands) != set(selected):
        raise ValueError("上游 pgbench 启动脚本缺少选中的负载")
    return commands


def render_properties(source, target, overrides):
    lines = [line for line in source.read_text(encoding="utf-8").splitlines()
             if line.split("=", 1)[0].strip() not in overrides]
    lines.extend(f"{key}={value}" for key, value in overrides.items())
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def jdbc_workspace(suite, output, selected, main_port, write_host, write_port):
    source = suite / "JDBC"
    work = output / "JDBC"
    work.mkdir(mode=0o700)
    (work / "logs").mkdir()
    (work / "resultlog").mkdir()
    commands = {}
    if "jdbc" in selected:
        for name in ("test_Jdbc.sh", "test_Jdbc.java"):
            shutil.copyfile(source / name, work / name)
        render_properties(source / "config.properties", work / "config.properties", {
            "db.host": "127.0.0.1", "db.port": main_port,
            "db.Writehost": write_host, "db.WritePort": write_port,
            "jdbc.drivers.dir": suite.parent / "lib_jdbc",
        })
        commands["jdbc"] = ["bash", "test_Jdbc.sh"]
    if "session_monitor" in selected:
        jars = sorted((suite.parent / "lib_jdbc").rglob("postgresql-*.jar"))
        if not jars:
            raise ValueError("上游会话巡检找不到 PostgreSQL JDBC 驱动")
        shutil.copyfile(source / "SessionMonitor.java", work / "SessionMonitor.java")
        render_properties(source / "session_monitor.properties", work / "session_monitor.properties", {
            "db.host": "127.0.0.1", "db.port": main_port,
        })
        subprocess.run(["javac", "-cp", str(jars[0]), "SessionMonitor.java"],
                       cwd=work, check=True, timeout=60)
        commands["session_monitor"] = [
            "java", "-cp", f".:{jars[0]}", "SessionMonitor", "session_monitor.properties",
        ]
    return work, commands


def stop_processes(processes):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for process in processes.values():
            # A shell may exit before its descendants. Signal the whole group.
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
        if sig == signal.SIGTERM and processes:
            time.sleep(0.5)
    for process in processes.values():
        process.wait(timeout=2)


def run(suite, output, duration, selected, main_port, write_port, db_host, db_port, pgbench_bin, memory=None):
    output.mkdir(mode=0o700, parents=True)
    launcher = (suite / "pgbench/call_testbench.sh.bak").read_text(encoding="utf-8")
    pgbench_password = next(
        (shlex.split(line)[0].partition("=")[2] for line in launcher.splitlines()
         if line.lstrip().startswith("PGPASSWORD=")), "",
    )
    pgbench = pgbench_commands(
        suite, duration, [name for name in selected if name.startswith("pgbench")], main_port, write_port,
    )
    pgbench_dir = output / "pgbench"
    pgbench_dir.mkdir()
    for name in pgbench:
        asset = f"test{name[7:]}.sql"
        shutil.copyfile(suite / "pgbench" / asset, pgbench_dir / asset)
    if any(name in selected for name in ("jdbc", "session_monitor")):
        jdbc_dir, jdbc = jdbc_workspace(suite, output, selected, main_port, db_host, db_port)
    else:
        jdbc_dir, jdbc = output, {}
    print(f"常稳时长 {duration} 秒，原始日志 {output}", flush=True)
    processes = {}
    jdbc_round = None
    jdbc_round_deadline = 0
    jdbc_rounds = 0
    jdbc_next = 0
    jdbc_timeout = 1800
    if "jdbc" in jdbc:
        for line in (jdbc_dir / "config.properties").read_text(encoding="utf-8").splitlines():
            if line.startswith("JDBC_ROUND_TIMEOUT_SECONDS="):
                jdbc_timeout = int(line.split("=", 1)[1])
                break
        if jdbc_timeout <= 0:
            raise ValueError("JDBC 单轮超时须为正整数")

    with ExitStack() as files:
        try:
            for index, name in enumerate(selected, 1):
                log = files.enter_context((output / f"{name}.log").open("w", encoding="utf-8"))
                if name == "jdbc":
                    jdbc_log = log
                    print(f"[{index}/{len(selected)}] {name} 等待上游 JDBC 启动窗口", flush=True)
                    continue
                cwd = pgbench_dir if name in pgbench else jdbc_dir
                command = [str(pgbench_bin), *pgbench[name]] if name in pgbench else jdbc[name]
                env = {**os.environ, "PGPASSWORD": pgbench_password} if name in pgbench else None
                processes[name] = subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                                    stderr=subprocess.STDOUT, start_new_session=True)
                print(f"[{index}/{len(selected)}] {name} {WORKLOADS[name]} 已启动", flush=True)
                if name in pgbench and index < len(selected):
                    time.sleep(1)
            deadline = time.monotonic() + duration
            while True:
                if memory:
                    memory.sample()
                now = time.monotonic()
                for name, process in processes.items():
                    code = process.poll()
                    if name == "jdbc":
                        continue
                    if code is not None and (code != 0 or name in jdbc):
                        raise RuntimeError(f"{name} 提前退出，退出码 {code}，请查看对应日志")
                if jdbc_round is not None:
                    code = jdbc_round.poll()
                    if code is None and now >= jdbc_round_deadline:
                        raise RuntimeError("上游 JDBC 单轮执行超时，请查看 JDBC 日志")
                    if code is not None:
                        if code:
                            raise RuntimeError(f"上游 JDBC 轮次失败，退出码 {code}，请查看 JDBC 日志")
                        stop_processes({"jdbc": processes.pop("jdbc")})
                        jdbc_round = None
                        jdbc_rounds += 1
                        jdbc_next = now + 600
                        print(f"JDBC 第 {jdbc_rounds} 轮通过", flush=True)
                remaining = deadline - now
                if remaining <= 0:
                    break
                # Upstream avoids the first six minutes of each Beijing hour.
                if ("jdbc" in jdbc and jdbc_round is None and now >= jdbc_next
                        and datetime.now(ZoneInfo("Asia/Shanghai")).minute >= 6):
                    jdbc_round = subprocess.Popen(
                        jdbc["jdbc"], cwd=jdbc_dir, stdout=jdbc_log, stderr=subprocess.STDOUT,
                        env={**os.environ, "TZ": "Asia/Shanghai"}, start_new_session=True,
                    )
                    processes["jdbc"] = jdbc_round
                    jdbc_round_deadline = now + jdbc_timeout
                    print(f"JDBC 第 {jdbc_rounds + 1} 轮开始", flush=True)
                time.sleep(min(remaining, 0.2))
            for name in pgbench:
                code = processes[name].wait(timeout=15)
                result = parse_pgbench_result(
                    (output / f"{name}.log").read_text(encoding="utf-8", errors="replace"), code,
                )
                if not result["ok"]:
                    raise RuntimeError(
                        f"{name} 执行失败，事务 {result['transactions']}，失败 {result['failures']}，"
                        f"退出码 {code}，请查看对应日志"
                    )
                print(f"{name} 通过，事务 {result['transactions']}，失败 0", flush=True)
        finally:
            stop_processes(processes)

    if "jdbc" in selected and not jdbc_rounds:
        raise RuntimeError("运行窗口内未完成一轮 JDBC，请查看 JDBC 日志或延长时长")
    if "session_monitor" in selected:
        log = (output / "session_monitor.log").read_text(encoding="utf-8", errors="replace")
        if "会话异常" in log or log.count("会话正常") < 2:
            raise RuntimeError("独立会话巡检异常或证据不足，请查看会话监控日志")
    print("7×24 常稳负载执行完成", flush=True)


def render_upstream_proxy(suite, run_dir, ports, main_port, write_port, prom_port, license_dir):
    userlist = run_dir / "userlist.conf"
    shutil.copyfile(suite / "userlist.conf", userlist)
    userlist.chmod(0o600)
    conf = (suite / "test.conf.bak").read_text(encoding="utf-8")
    values = {
        "FBASECMAN_LICENSE_DIR": license_dir,
        "WRITE_PORT": write_port, "FBASECMAN_PORT": main_port,
        **{f"{name.upper()}_PORT": port for name, port in ports.items()},
        **{f"{name.upper()}_HOST": "127.0.0.1" for name in ports},
    }
    for name, value in values.items():
        conf = conf.replace(name, str(value))
    overrides = {
        "pid_file": f'"{run_dir / "fbasecman.pid"}"',
        "locks_dir": f'"{run_dir / "locks"}"',
        "log_directory": f'"{run_dir / "fbasecman-logs"}"',
        "auth_file": f'"{userlist}"',
        "host": '"127.0.0.1"',
    }
    for key, value in overrides.items():
        conf = re.sub(rf'(?m)^{key}[ \t]+[^\n]*$', lambda _, replacement=f"{key} {value}": replacement, conf)
    conf += f"\npromhttp_server_port {prom_port}\n"
    path = run_dir / "fbasecman.conf"
    path.write_text(conf, encoding="utf-8")
    return path


def run_platform(suite, regress_root, build, output, work_root, database_license, duration, selected, profile_interval=None):
    import yaml
    from platform_regress.execution.command import run_logged_command

    from products.fbasecman.longrun_environment import HOST, isolated_environment
    from products.fbasecman.longrun_memory import MemoryProfile
    from products.fbasecman.process import FbasecmanProcess

    # Keep the existing stable runtime's port allocation policy.
    sys.path.insert(0, str(regress_root))
    from suites.stable.runtime import _port_free, choose_ports

    task = str(UUID(os.environ["PRODUCT_PLATFORM_TASK_ID"]))
    run_dir = output / "runs" / task
    run_dir.mkdir(mode=0o700, parents=True)
    proxy_dir = run_dir / "stability"
    proxy_dir.mkdir()
    product_logs = proxy_dir / "fbasecman-logs"
    product_logs.mkdir()
    config = yaml.safe_load(build.read_text(encoding="utf-8"))["fbasecman"]
    memory = None
    if profile_interval is not None:
        memory = MemoryProfile(work_root.parent, run_dir / "memory", config["fbasecman_bin"], profile_interval)
    print("正在准备上游长稳同款六节点 MMR、PostgreSQL 参数和 pg_cron", flush=True)
    with isolated_environment(suite, work_root / task, database_license) as (home, ports):
        main_port, write_port, prom_port = choose_ports()
        proxy = FbasecmanProcess(
            config["fbasecman_bin"], home, main_port, prom_port,
            proxy_dir / "fbasecman.pid", proxy_dir / "locks",
            product_logs / "fbasecman.log", proxy_dir,
            execute=lambda argv, log_path, **kwargs: _run_proxy_command(
                run_logged_command, argv, log_path, proxy_dir, kwargs.get("env"),
            ),
            trace=lambda message: print(message, flush=True), port_is_free=_port_free,
        )
        conf = render_upstream_proxy(suite, proxy_dir, ports, main_port, write_port, prom_port, config["license_dir"])
        try:
            proxy.start(conf, ready_timeout=40, env=memory.env if memory else None)
            if memory:
                memory.attach(proxy_dir / "fbasecman.pid")
            run(suite, run_dir / "longrun", duration, selected, main_port, write_port,
                HOST, ports["mmr1"], home / "bin/pgbench", memory)
        finally:
            try:
                if memory:
                    memory.sample(force=True)
            finally:
                proxy.stop(best_effort=False, conf=conf, record=False)
            if memory:
                memory.finish()


def _run_proxy_command(run_logged_command, argv, log_path, cwd, env):
    result = run_logged_command(argv, log_path, cwd=cwd, env=env, timeout=45)
    return result.returncode, result.output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("suite", type=Path)
    parser.add_argument("regress_root", type=Path)
    parser.add_argument("build", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("lock_dir", type=Path)
    parser.add_argument("duration", type=int)
    parser.add_argument("workloads", nargs="+")
    parser.add_argument("--database-license", type=Path)
    parser.add_argument("--profile-interval", type=int, choices=range(20, 37))
    args = parser.parse_args()

    def interrupted(_signum, _frame):
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, interrupted)
    try:
        duration, selected = selected_workloads({
            "duration_seconds": args.duration, "enabled_workloads": args.workloads,
        })
        with ExclusiveFileLock(args.lock_dir / "isolated.lock", "平台常稳环境"):
            run_platform(args.suite, args.regress_root, args.build, args.output,
                         args.lock_dir, args.database_license, duration, selected, args.profile_interval)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"7×24 常稳执行失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

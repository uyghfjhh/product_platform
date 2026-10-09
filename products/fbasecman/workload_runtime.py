"""Owned fbasecman lifecycle and paired direct/proxy query measurements.

Like stable.sh: isolated config/logs/ports, console + business preflight,
then workload, finally stop only this run's process. No database lifecycle.
The comparison pins one backend (single group) rather than balancing reads
across nodes: it measures proxy overhead, not MMR routing throughput.
"""
from __future__ import annotations

import json
import hashlib
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from platform_app.workloads import runner


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.part')
    temporary.write_text(json.dumps(value, ensure_ascii=False))
    temporary.replace(path)


def comparison(direct, proxy):
    if direct.get('status') != 'PASS' or proxy.get('status') != 'PASS':
        return {'valid': False, 'reason': '直连或代理阶段失败，不计算性能损失'}
    d, p = direct['summary'], proxy['summary']
    dt, pt, dl, pl = (d.get('tps'), p.get('tps'), d.get('latency_ms'), p.get('latency_ms'))
    if not dt or not pt or dl is None or pl is None:
        return {'valid': False, 'reason': '完整 TPS 或平均延迟缺失，不计算性能损失'}
    return {'valid': True, 'direct_tps': dt, 'proxy_tps': pt,
            'tps_loss_percent': (dt-pt)/dt*100,
            'direct_latency_ms': dl, 'proxy_latency_ms': pl,
            'latency_increase_percent': (pl-dl)/dl*100 if dl > 0 else None,
            'latency_increase_ms': pl-dl,
            'reason': '同一后端、SQL、并发、连接方式和时长；先直连后代理，各预热 3 秒。百分比为本轮观测，负值表示代理阶段更快。'}


def render_config(output, backend, runtime, port, identity):
    q = json.dumps
    return '\n'.join([
        f'pid_file {q(str(output / "fbasecman.pid"))}', 'daemonize no',
        f'locks_dir {q(str(output / "locks"))}', 'unix_socket_dir "/tmp"',
        'unix_socket_mode "0644"', f'license_dir {q(runtime["license_dir"])}',
        'coroutine_stack_size 16', 'workers 8', 'resolvers 1', 'nodelay yes',
        'enable_guc_sync yes', 'heartbeat_request "SELECT 10086"',
        'log_to_stdout no', 'log_syslog no', 'log_debug no', 'log_config yes',
        'log_session no', 'log_query no', 'log_stats yes', 'stats_interval 15',
        'log_min_messages "info"', 'log_format "%p %t %l [%i %s] (%c) %m\\n"',
        f'log_file {q(str(output / "fbasecman.log"))}',
        'host "127.0.0.1"', f'ports "{port}"', f'promhttp_server_port {port+2}',
        'admin_database "console"', 'monitor_enabled yes',
        'monitor_retry_period_ms 1000', 'monitor_period 10',
        'datasources "baseline_node" {', f'    host {q(backend["host"])}',
        f'    port {backend["port"]}', '    cluster_name "baseline_cluster"',
        '    status "active"', '    weight 10', f'    system_identifier "{identity}"',
        '    tls "disable"', '}',
        'group "baseline" {', '    group_mode "single"',
        f'    storage_db {q(backend["database_name"])}',
        '    backend_clusters "baseline_cluster"', '    access_mode "read_write"',
        '    check "auto"', '}',
        f'user {q(backend["database_user"])} {{', '    group_names "baseline"',
        '    authentication "none"', f'    storage_user {q(backend["database_user"])}',
        '    rw_split_method "none"', '    pool "transaction"', '    pool_size 128',
        '    pool_discard no', '    pool_reserve_prepared_statement yes',
        '    server_lifetime 60', '}',
        'user "admin" {', '    authentication "none"', '    pool "session"',
        '    role "admin"', '}', '',
    ])


def query(psql, endpoint, sql, *, formatted=False):
    result = subprocess.run([psql, '-X', '-h', endpoint['host'], '-p', str(endpoint['port']),
                             '-U', endpoint['database_user'], '-d', endpoint['database_name'],
                             '-v', 'ON_ERROR_STOP=1',
                             *(['-P', 'pager=off'] if formatted else ['-At']), '-c', sql],
                            capture_output=True, text=True, timeout=8)
    if result.returncode:
        raise ValueError(result.stderr.strip() or '连接验证失败')
    return result.stdout.strip()


def sample_process(process, output, stop):
    rows, previous = [], None
    while not stop.is_set():
        try:
            stat = Path(f'/proc/{process.pid}/stat').read_text().rsplit(')', 1)[1].split()
            ticks = int(stat[11]) + int(stat[12])
            now = time.monotonic()
            cpu = (ticks-previous[1])/os.sysconf('SC_CLK_TCK')/(now-previous[0])*100 if previous else None
            previous = (now, ticks)
            rows.append({'observed_at': time.time(), 'pid': process.pid,
                         'rss_mib': int(stat[21])*os.sysconf('SC_PAGE_SIZE')/1048576,
                         'cpu_percent': cpu})
            write_json(output / 'proxy-monitor.json', {'pid': process.pid, 'running': process.poll() is None,
                                                      'samples': rows[-600:]})
        except (OSError, ValueError, IndexError):
            break
        stop.wait(1)


def main(request_path):
    request = json.loads(Path(request_path).read_text())
    output, backend, runtime = Path(request['output']), request['environment'], request['product_runtime']
    mode = request['options']['connection_mode']
    proxy_process = None
    stop = threading.Event()
    monitor = None
    phases = {}
    samples = []
    count = 0
    started = time.time()
    route_sql = "SELECT inet_server_port(), current_database(), system_identifier::text FROM pg_control_system()"
    evidence = {'mode': mode, 'backend': backend, 'phases': {}, 'proxy_started': False,
                'tested_build': {key: runtime.get(key) for key in
                                 ('build_id', 'build_name', 'binary', 'resolved_path', 'sha256', 'version', 'license_dir', 'source')}}
    def interrupted(_signum, _frame):
        raise InterruptedError('负载已取消')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        baseline = query(runtime['psql'], backend, route_sql)
        identity = baseline.split('|')[-1]
        if not identity.isdigit():
            raise ValueError('后端 system_identifier 无效')
        evidence['backend_identity'] = baseline
        # Keep both listener and metrics ports reserved until immediately before launch.
        for _ in range(50):
            listener, metrics = socket.socket(), socket.socket()
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
            try:
                metrics.bind(('127.0.0.1', port+2))
                break
            except (OSError, OverflowError):
                listener.close(); metrics.close()
        else:
            raise ValueError('无法分配独立代理端口')
        config = output / 'fbasecman.conf'
        config.write_text(render_config(output, backend, runtime, port, identity))
        proxy = {**backend, 'host': '127.0.0.1', 'port': port, 'database_name': 'baseline'}
        evidence['proxy'] = proxy
        listener.close(); metrics.close()
        with (output / 'fbasecman-start.log').open('w') as log:
            proxy_process = subprocess.Popen([runtime['binary'], str(config)], cwd=output,
                                             stdout=log, stderr=subprocess.STDOUT)
        evidence.update(proxy_started=True, proxy_pid=proxy_process.pid)
        executable = Path(f'/proc/{proxy_process.pid}/exe')
        digest = hashlib.sha256()
        with executable.open('rb') as file:
            for chunk in iter(lambda: file.read(1048576), b''):
                digest.update(chunk)
        evidence['loaded_binary_sha256'] = digest.hexdigest()
        if runtime.get('sha256') and evidence['loaded_binary_sha256'] != runtime['sha256']:
            raise ValueError('启动的 fbasecman 构建与审阅快照不一致')
        write_json(output / 'connection-evidence.json', evidence)
        console = {**proxy, 'database_user': 'admin', 'database_name': 'console'}
        deadline = time.monotonic()+40
        while True:
            if proxy_process.poll() is not None:
                raise ValueError('fbasecman 启动失败，请检查 fbasecman.log 和 fbasecman-start.log')
            try:
                route = query(runtime['psql'], proxy, route_sql)
                if route != baseline:
                    raise RuntimeError('代理实际后端与直连后端不一致，禁止性能对比')
                evidence['proxy_backend_identity'] = route
                evidence['show_datasources'] = query(runtime['psql'], console, 'SHOW DATASOURCES;', formatted=True)
                evidence['show_pools_before'] = query(runtime['psql'], console, 'SHOW POOLS;', formatted=True)
                break
            except (ValueError, subprocess.TimeoutExpired):
                if time.monotonic() >= deadline:
                    raise ValueError('fbasecman 业务连接在 40 秒内未就绪')
                time.sleep(.5)
        print('FBASECMAN_READY '+json.dumps(evidence, ensure_ascii=False), flush=True)
        write_json(output / 'connection-evidence.json', evidence)
        monitor = threading.Thread(target=sample_process, args=(proxy_process, output, stop), daemon=True)
        monitor.start()
        for phase in (['direct', 'proxy'] if mode == 'compare' else ['proxy']):
            endpoint = backend if phase == 'direct' else proxy
            evidence['phases'][phase] = {'endpoint': endpoint, 'backend_identity': query(runtime['psql'], endpoint, route_sql)}
            if evidence['phases'][phase]['backend_identity'] != baseline:
                raise ValueError('测试阶段后端身份改变')
            write_json(output / 'connection-evidence.json', evidence)
            for warmup in (True, False):
                directory = output / (phase+'-warmup' if warmup else phase)
                directory.mkdir(exist_ok=True)
                child = {**request, 'output': str(directory), 'environment': endpoint,
                         'options': {**request['options']}, 'phase': phase}
                if warmup:
                    child['options'].update(duration_seconds=3, minimum_tps=0, max_average_latency_ms=0)
                else:
                    child.update(metrics_sink=str(output / 'metrics.json'), previous_samples=samples,
                                 previous_sample_count=count)
                path = directory / 'request.json'
                write_json(path, child)
                print(f'PHASE={phase} '+('WARMUP' if warmup else 'MEASURED'), flush=True)
                code = runner.main(path)
                result = json.loads((directory / 'result.json').read_text())
                if not warmup:
                    phases[phase] = result
                    metrics_value = json.loads((directory / 'metrics.json').read_text())
                    samples = samples + metrics_value['samples']
                    count += metrics_value['sample_count']
                if code:
                    raise ValueError(f'{phase} '+('预热失败' if warmup else '测量失败')+'；停止后续阶段')
            if proxy_process.poll() is not None:
                raise ValueError('fbasecman 在负载执行期间退出')
            if query(runtime['psql'], endpoint, route_sql) != baseline:
                raise ValueError('阶段结束时后端身份改变，测量无效')
        evidence['show_pools_after'] = query(runtime['psql'], console, 'SHOW POOLS;', formatted=True)
        evidence['proxy_version'] = query(runtime['psql'], console, 'SHOW VERSION;')
        result = {**phases['proxy'], 'started_at': started, 'finished_at': time.time(),
                  'connection_mode': mode, 'phases': phases, 'connection_evidence': evidence}
        if mode == 'compare':
            result['comparison'] = comparison(phases['direct'], phases['proxy'])
        write_json(output / 'result.json', result)
        write_json(output / 'metrics.json', {'samples': samples, 'sample_count': count,
                   'summary': result['summary'], 'connection_mode': mode, 'phase': 'proxy'})
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        result = {'status': 'FAIL', 'reason': str(exc), 'connection_mode': mode,
                  'started_at': started, 'finished_at': time.time(), 'phases': phases,
                  'comparison': {'valid': False, 'reason': '阶段失败或取消，不计算性能损失'},
                  'checks': [{'name': '代理生命周期及后端一致性', 'expected': '就绪且同一后端', 'actual': str(exc), 'passed': False}]}
        write_json(output / 'result.json', result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 1
    finally:
        stop.set()
        if monitor:
            monitor.join(timeout=2)
        if proxy_process:
            if proxy_process.poll() is None:
                proxy_process.terminate()
                try:
                    proxy_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proxy_process.kill(); proxy_process.wait(timeout=5)
            evidence.update(proxy_stopped=True, proxy_returncode=proxy_process.returncode)
            monitor_path = output / 'proxy-monitor.json'
            if monitor_path.is_file():
                process_samples = json.loads(monitor_path.read_text())
                process_samples['running'] = False
                write_json(monitor_path, process_samples)
        write_json(output / 'connection-evidence.json', evidence)
        if (output / 'result.json').is_file():
            final = json.loads((output / 'result.json').read_text())
            final['connection_evidence'] = evidence
            write_json(output / 'result.json', final)
            write_report(output, final, request)


def write_report(output, result, request):
    evidence = result.get('connection_evidence', {})
    build = evidence.get('tested_build', {})
    lines = ['# fbasecman 查询常稳 / 性能对比', '',
             f'执行结果：{result["status"]}。{result.get("reason", "")}', '',
             f'被测构建：{build.get("version", "未记录")}',
             f'文件：`{build.get("binary", "未记录")}`',
             f'SHA256：`{build.get("sha256", "未记录")}`', '',
             f'模式：{request["options"]["connection_mode"]}；并发 {request["options"]["clients"]}；每阶段计时 {request["options"]["duration_seconds"]} 秒；各预热 3 秒。',
             '```sql', request['options']['script'], '```', '']
    paired = result.get('comparison')
    if paired:
        lines += [paired['reason'], '']
        if paired['valid']:
            latency = paired['latency_increase_percent']
            lines += ['| 指标 | 数据库直连 | fbasecman | 相对变化 |',
                      '| --- | ---: | ---: | ---: |',
                      f'| TPS | {paired["direct_tps"]:.2f} | {paired["proxy_tps"]:.2f} | 吞吐损失 {paired["tps_loss_percent"]:.2f}% |',
                      f'| 平均延迟 ms | {paired["direct_latency_ms"]:.3f} | {paired["proxy_latency_ms"]:.3f} | '+
                      (f'延迟增幅 {latency:.2f}%' if latency is not None else '百分比不可计算')+' |', '',
                      '吞吐损失 = (直连 TPS − 代理 TPS) / 直连 TPS × 100%。',
                      '单轮顺序测量受缓存与后台活动影响；短时试跑只验证功能，正式评价需长时、多轮复测。', '']
    lines += [f'直连后端身份：`{evidence.get("backend_identity", "未验证")}`',
              f'代理后端身份：`{evidence.get("proxy_backend_identity", "未验证")}`',
              '验证字段依次为后端端口、数据库名、system identifier；三项一致才允许继续测量。',
              f'本轮代理 PID：{evidence.get("proxy_pid", "未启动")}；停止确认：{evidence.get("proxy_stopped", False)}。', '']
    for key, command in [('show_datasources', 'SHOW DATASOURCES;'),
                         ('show_pools_before', 'SHOW POOLS; -- 负载前'),
                         ('show_pools_after', 'SHOW POOLS; -- 负载后')]:
        if evidence.get(key):
            lines += ['```text', command, evidence[key], '```', '']
    lines += ['配置与原始证据：fbasecman.conf、fbasecman.log、connection-evidence.json、proxy-monitor.json；各阶段的原始采样在 direct/ 与 proxy/ 目录。', '']
    (output / 'report.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))

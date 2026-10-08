"""Build a test-only proxy from real product objects and link wrappers."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
from pathlib import Path

from platform_regress.sdk import Blocked

WRAPPED = (
    'fb_guc_cache_insert_or_update', 'fb_guc_cache_copy', 'fb_guc_cache_clear', 'fb_guc_cache_free',
    'fb_add_outstanding_request', 'machine_msg_create', 'machine_iov_add',
    'machine_iov_add_pointer', 'machine_msg_write', 'mm_io_write',
    'mm_socket_writev', 'fb_sql_parse_client_state_prepare_forbidden', 'od_reset',
)


def build_test_proxy(context):
    cached = context.values.get('guc_test_proxy')
    if cached:
        return Path(cached)
    original = Path(context.environment['fbasecman_bin']).resolve()
    build_sources = original.parent
    link_file = build_sources / 'CMakeFiles/fbasecman.dir/link.txt'
    flags_file = link_file.with_name('flags.make')
    if not link_file.is_file() or not flags_file.is_file():
        raise Blocked('内部测试需要被测构建的 link.txt/flags.make 和对象文件；不是等待业务修复')
    root = context.output_dir / 'instrumentation'
    root.mkdir(parents=True, exist_ok=True)
    asset = Path(__file__).parent / 'regression/suites/guc/assets/guc_probe_wrap.c'
    flags = {}
    for line in flags_file.read_text().splitlines():
        if ' = ' in line:
            key, value = line.split(' = ', 1)
            flags[key] = shlex.split(value)
    obj = root / 'guc_probe_wrap.o'
    compile_argv = ['/usr/bin/cc', *flags.get('C_DEFINES', []), *flags.get('C_INCLUDES', []),
                    *flags.get('C_FLAGS', []), '-c', str(asset.resolve()), '-o', str(obj.resolve())]
    result = context.command(compile_argv, cwd=build_sources, timeout_seconds=60)
    if result.returncode:
        raise RuntimeError(f'测试拦截器编译失败：{result.stdout}\n{result.stderr}')
    link = shlex.split(link_file.read_text())
    output = root / 'fbasecman-guc-test'
    index = link.index('-o')
    link[index+1] = str(output.resolve())
    link += [str(obj.resolve()), '-rdynamic', *[f'-Wl,--wrap={name}' for name in WRAPPED]]
    result = context.command(link, cwd=build_sources, timeout_seconds=60)
    if result.returncode:
        raise RuntimeError(f'测试代理链接失败：{result.stdout}\n{result.stderr}')
    context.attach_text('instrumented-build.json', json.dumps({
        'original_binary': str(original), 'original_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
        'test_binary_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'wrapper_sha256': hashlib.sha256(asset.read_bytes()).hexdigest(),
        'link_command': link, 'compile_command': compile_argv,
        'note': '原产品对象与业务逻辑未修改，仅增加观测及故障注入拦截',
    }, ensure_ascii=False, indent=2))
    context.values['guc_test_proxy'] = str(output.resolve())
    return output.resolve()


class ProductChecks:
    def __init__(self, runner, config, binary):
        self.runner = runner
        self.context = runner.context
        self.config = config.resolve()
        self.binary = binary.resolve()
        self.folder = self.context.output_dir / 'instrumentation' / runner.plan.key
        self.folder.mkdir(parents=True, exist_ok=True)
        self.trace = self.folder / 'trace.jsonl'
        self.stage_file = self.folder / 'stage.txt'
        self.serial = 0

    def stage(self, name):
        self.stage_file.write_text(name)

    def start(self, fault=''):
        self.context.stop_processes()
        self.serial += 1
        self.trace = self.folder / f'trace-{self.serial}.jsonl'
        self.stage('prepare')
        self.process = self.context.start_process([str(self.binary), str(self.config)], cwd=self.folder, ready_host=self.runner.host,
                                   ready_port=self.runner.port, env={**os.environ,
                                       'FB_GUC_TEST_TRACE': str(self.trace.resolve()),
                                       'FB_GUC_TEST_STAGE': str(self.stage_file.resolve()),
                                       'FB_GUC_TEST_FAIL': fault,
                                   })

    def events(self, stage=None):
        if not self.trace.exists():
            return []
        records = [json.loads(line) for line in self.trace.read_text().splitlines() if line]
        return [r for r in records if stage is None or r['stage'] == stage]

    def verify(self, title, expected, actual, passed):
        if self.trace.exists():
            self.context.attach_file(f'{self.runner.plan.key}-trace-{self.serial}.jsonl', self.trace)
        self.runner.verify(title, expected, actual, passed)

    def boundary(self):
        from products.fbasecman.guc_alignment_native import wire
        self.start()
        with self.runner.client('cache') as p:
            self.runner.baseline(p)
            for phase, packet in (
                ('P', wire.message('P', wire.parse_payload('cache_stmt', "SET work_mem='32MB'"))),
                ('B', wire.message('B', wire.bind_payload('cache_portal', 'cache_stmt'))),
                ('DS', wire.message('D', wire.describe_statement_payload('cache_stmt'))),
                ('DP', wire.message('B', wire.bind_payload('cache_portal2', 'cache_stmt')) +
                       wire.message('D', wire.describe_portal_payload('cache_portal2'))),
            ):
                self.stage(phase)
                p.exchange(packet + wire.sync_message(), f'{phase} 内部缓存不提前写', {'tags': [], 'sqlstates': [], 'ready': ['I']})
                records = self.events(phase)
                mutations = [r for r in records if r['event'] in {'cache_after', 'cache_copy', 'cache_clear'}
                             and r['cache'] in {'frontend', 'transaction'}]
                self.verify(f'{phase} 正式及工作缓存不变', [], mutations, not mutations)
                self.stage('observe')
                p.snapshot({'work_mem': '8MB'})
            self.stage('E')
            p.exchange(wire.message('B', wire.bind_payload('', 'cache_stmt')) +
                       wire.message('E', wire.execute_payload('')) + wire.sync_message(),
                       'E 后真实缓存提交', {'tags': ['SET'], 'sqlstates': [], 'ready': ['I']})
            records = self.events('E')
            changes = [r for r in records if r['event'] == 'cache_after' and r['cache'] == 'frontend' and r['key'] == 'work_mem' and r['result']]
            self.verify('E 正式缓存写入当前候选', '至少一次 frontend work_mem 写入', changes, bool(changes))
            self.stage('observe')
            p.snapshot({'work_mem': '32MB'})
        # Separate E/Q transactions prove working-cache writes and no formal
        # promotion before COMMIT; observations are real wrapper events.
        for protocol in ('Q', 'E'):
            for ending in ('COMMIT', 'ROLLBACK'):
                with self.runner.client(f'{protocol}_{ending}') as p:
                    self.stage('prepare')
                    self.runner.baseline(p)
                    p.sql('BEGIN', tag='BEGIN', ready='T', protocol=protocol)
                    record_stage = f'record_{protocol}_{ending}'
                    self.stage(record_stage)
                    p.sql("SET work_mem='32MB'", tag='SET', ready='T', protocol=protocol)
                    records = self.events(record_stage)
                    working = [r for r in records if r['event'] == 'cache_after' and r['cache'] == 'transaction' and r['key'] == 'work_mem' and r['result']]
                    formal = [r for r in records if r['event'] == 'cache_after' and r['cache'] == 'frontend' and r['key'] == 'work_mem']
                    self.verify(f'{protocol} 预记录工作缓存，正式缓存不变', {'working_writes': 1, 'formal_writes': 0},
                                {'working': working, 'formal': formal}, len(working) == 1 and not formal)
                    end_stage = f'end_{protocol}_{ending}'
                    self.stage(end_stage)
                    p.sql(ending, tag=ending, protocol=protocol)
                    records = self.events(end_stage)
                    promoted = [r for r in records if r['event'] == 'cache_copy' and r['cache'] == 'frontend']
                    self.verify(f'{protocol}/{ending} 正式提升边界', ending == 'COMMIT', promoted,
                                bool(promoted) == (ending == 'COMMIT'))
                    self.stage('observe')
                    p.snapshot({'work_mem': '32MB' if ending == 'COMMIT' else '8MB'})

        # Batch Q is a separate recording path; preserve ordered mutation
        # events through segment commit/rollback instead of testing only Q1.
        with self.runner.client('batch_cache') as p:
            self.stage('prepare')
            self.runner.baseline(p)
            self.stage('batch')
            p.sql("BEGIN; SET work_mem='16MB'; COMMIT; SET work_mem='64MB'; SELECT 1/0;",
                  protocol='Q', error='22012')
            records = self.events('batch')
            promotions = [r for r in records if r['event'] == 'cache_copy' and r['cache'] == 'frontend']
            self.verify('多语句 Q 前段提升且失败后段不提升', '16MB 的提交，不提升64MB', promotions,
                        bool(promotions) and all('64MB' not in r['value'] for r in promotions))
            self.stage('observe')
            p.snapshot({'work_mem': '16MB'})
        # A real old backend is present in session mode; transaction mode
        # uses the reset/detach events to distinguish it from a new attach.
        with self.runner.client('reclaim_cache') as p:
            self.stage('prepare')
            p.snapshot()
            self.stage('reclaim')
            p.sql("SET work_mem='32MB'", protocol='E', tag='SET')
            p.snapshot({'work_mem': '32MB'})
            events = self.events('reclaim')
            backend_changes = [r for r in events if r['event'] == 'cache_after' and r['cache'] == 'backend' and r['key'] == 'work_mem']
            frontend_changes = [r for r in events if r['event'] == 'cache_after' and r['cache'] == 'frontend' and r['key'] == 'work_mem']
            self.verify('本地 E 正式提交与真实部署分别取证', '正式写入后才由后端部署写入', events,
                        bool(frontend_changes) and (not backend_changes or events.index(frontend_changes[0]) < events.index(backend_changes[-1])))

    def faults(self, points):
        from products.fbasecman.guc_alignment_native import (
            extended_packet,
            response_facts,
            wire,
        )
        failures = []
        for protocol, point in points:
            self.start(point)
            try:
                with self.runner.client(f'{protocol}_{point}') as p:
                    self.runner.baseline(p)
                    if point in {'pre_record', 'pending', 'outstanding', 'forward'}:
                        p.sql('BEGIN', tag='BEGIN', ready='T', protocol=protocol)
                    self.stage('fault')
                    packet = extended_packet("SET work_mem='32MB'") if protocol == 'E' else wire.simple_query_message("SET work_mem='32MB'")
                    p.sock.sendall(packet)
                    messages = []
                    disconnected = False
                    try:
                        while True:
                            kind, payload = wire.read_message(p.sock)
                            messages.append((kind, payload))
                            if kind == 'Z':
                                break
                    except TimeoutError:
                        self.verify(f'{protocol}/{point} 不能以超时代替断连', '明确断连或错误收口', response_facts(messages), False)
                    except (OSError, RuntimeError):
                        disconnected = True
                    facts = response_facts(messages)
                    self.context.attach_text(f'{self.runner.plan.key}-{protocol}-{point}-wire.json', json.dumps({
                        'sent_hex': packet.hex(), 'received': [{'kind': k, 'payload_hex': b.hex()} for k, b in messages],
                        'facts': facts, 'disconnected': disconnected}, ensure_ascii=False, indent=2))
                    records = self.events('fault')
                    injected = [r for r in records if r['event'] == 'injected' and r['key'] == point]
                    # Injection not reached is a test failure on current code,
                    # not a permanent BLOCKED gate after development.
                    self.verify(f'{protocol}/{point} 故障点确实触发', 1, injected, len(injected) == 1)
                    self.verify('故障后代理进程仍可服务其他客户端', None, self.process.poll(), self.process.poll() is None)
                    self.verify(f'{protocol}/{point} 不发送错误成功响应', [], facts['tags'], not facts['tags'])
                    if point in {'apply', 'response_queue', 'response_construct'}:
                        self.verify(f'{point} 失败后终止连接', True, disconnected, disconnected)
                    promotions = [r for r in records if r['event'] == 'cache_copy' and r['cache'] == 'frontend']
                    self.verify(f'{protocol}/{point} 失败候选不提升', [], promotions, not promotions)
                    # An unexpected surviving connection must not commit.
                    if not disconnected and point in {'pre_record', 'pending', 'outstanding', 'forward'}:
                        self.stage('after_failure')
                        p.sock.sendall(wire.simple_query_message('COMMIT'))
                        follow = []
                        try:
                            while True:
                                k, body = wire.read_message(p.sock); follow.append((k, body))
                                if k == 'Z': break
                        except (OSError, RuntimeError):
                            pass
                        self.verify('失败后不得成功提交未知状态', False,
                                    response_facts(follow), 'COMMIT' not in response_facts(follow)['tags'])
            except AssertionError as exc:
                failures.append(str(exc))
                # A failed fault is isolated in its own proxy; continue every
                # independent injection rather than skipping the rest.
            finally:
                if self.trace.exists():
                    self.context.attach_file(f'{self.runner.plan.key}-{protocol}-{point}.jsonl', self.trace)
        if failures:
            raise AssertionError('; '.join(failures))

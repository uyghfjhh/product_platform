"""Actual pgbench/JDBC execution, metric sampling and deterministic thresholds."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

from pglast import parse_sql
from pglast.stream import RawStream

PRESETS = {
    "connectivity": "SELECT 1;",
    "catalog": "SELECT count(*) FROM pg_catalog.pg_class;",
}


def main(path):
    request = json.loads(Path(path).read_text())
    output = Path(request["output"])
    options = request["options"]
    environment = request["environment"]
    driver = request["driver"]
    env = dict(os.environ)
    duration = options["duration_seconds"]
    summary = {}
    samples = []
    sample_count = 0
    if driver == "pgbench":
        script = output / "workload.sql"
        sql = options.get('script') or PRESETS[options['preset']]
        script.write_text('BEGIN READ ONLY;\n' + RawStream()(parse_sql(sql)).rstrip(';') + ';\nCOMMIT;\n')
        env['PGOPTIONS'] = (
            '-c default_transaction_read_only=on -c statement_timeout='
            + str(options.get('statement_timeout_seconds', 10) * 1000)
        )
        command = [
            request["pgbench"],
            "-h",
            environment["host"],
            "-p",
            str(environment["port"]),
            "-U",
            environment["database_user"],
            "-n",
            "-f",
            str(script),
            "-c",
            str(options["clients"]),
            "-j",
            str(options.get('jobs') or min(options['clients'], 16)),
            "-T",
            str(duration),
            "-P",
            "1",
            environment["database_name"],
        ]
        extra = []
        if options.get('connect_per_transaction'):
            extra.append('-C')
        if options.get('target_tps'):
            extra.extend(['-R', str(options['target_tps'])])
        command[-1:-1] = extra
    else:
        classes = output / "classes"
        classes.mkdir(exist_ok=True)
        subprocess.run(
            [
                "javac",
                "-d",
                str(classes),
                str(Path(__file__).with_name("JdbcLoad.java")),
            ],
            check=True,
        )
        host = environment["host"]
        host = "[" + host + "]" if ":" in host else host
        env.update(
            JDBC_URL="jdbc:postgresql://"
            + host
            + ":"
            + str(environment["port"])
            + "/"
            + quote(environment["database_name"]),
            JDBC_USER=environment["database_user"],
            JDBC_SQL=options.get('script') or PRESETS[options['preset']],
            JDBC_STATEMENT_TIMEOUT=str(options.get('statement_timeout_seconds', 10)),
        )
        command = [
            "java",
            "-cp",
            str(classes) + os.pathsep + options["jdbc_jar"],
            "JdbcLoad",
            str(options["clients"]),
            str(duration),
        ]
    started = time.time()
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env
    )
    try:
        for line in process.stdout:
            print(line.rstrip(), flush=True)
            if driver == "jdbc" and line.startswith("{"):
                row = json.loads(line)
                if row["type"] == "sample":
                    samples.append(row)
                    sample_count += 1
                else:
                    summary = row
            elif driver == "pgbench":
                match = re.search(
                    r"progress:\s*([\d.]+) s, ([\d.]+) tps, lat ([\d.]+) ms", line
                )
                if match:
                    samples.append(
                        {
                            "elapsed": float(match[1]),
                            "tps": float(match[2]),
                            "latency_ms": float(match[3]),
                        }
                    )
                    sample_count += 1
                match = re.search(r"^tps = ([\d.]+)", line)
                if match:
                    summary["tps"] = float(match[1])
                match = re.search(r"^latency average = ([\d.]+) ms", line)
                if match:
                    summary["latency_ms"] = float(match[1])
                match = re.search(r'^number of failed transactions:\s*(\d+)', line)
                if match:
                    summary['errors'] = int(match[1])
            if len(samples) > 600:
                samples = samples[-600:]
            temporary = output / "metrics.part"
            temporary.write_text(json.dumps({'samples': samples, 'summary': summary, 'sample_count': sample_count}))
            temporary.replace(output / "metrics.json")
        code = process.wait(timeout=15)
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
    checks = [
        {'name': '客户端正常结束', 'expected': 0, 'actual': code, 'passed': code == 0},
        {'name': '产生实际吞吐采样', 'expected': '> 0 TPS', 'actual': summary.get('tps'),
         'passed': summary.get('tps', 0) > 0},
    ]
    if 'errors' in summary:
        checks.append({'name': '客户端报告错误数', 'expected': 0, 'actual': summary['errors'],
                       'passed': summary['errors'] == 0})
    thresholds = []
    if options['minimum_tps']:
        thresholds.append({'name': '最低 TPS', 'expected': options['minimum_tps'],
                           'actual': summary.get('tps'),
                           'passed': summary.get('tps', 0) >= options['minimum_tps']})
    if options['max_average_latency_ms']:
        thresholds.append({'name': '最高平均延迟 ms', 'expected': options['max_average_latency_ms'],
                           'actual': summary.get('latency_ms'),
                           'passed': summary.get('latency_ms', float('inf')) <= options['max_average_latency_ms']})
    success = all(item['passed'] for item in checks + thresholds)
    result = {
        'execution_id': request['execution_id'], 'driver': driver,
        'started_at': started, 'finished_at': time.time(),
        'status': 'PASS' if success else 'FAIL', 'summary': summary,
        'checks': checks + thresholds,
        'correctness_verdict': 'NOT_CONFIGURED',
        'performance_verdict': ('PASS' if all(item['passed'] for item in thresholds) else 'FAIL')
        if thresholds else 'NOT_CONFIGURED',
        'reason': ('执行与阈值验收通过' if thresholds else '执行完成；未配置性能阈值及业务正确性断言')
        if success else '客户端执行失败或指标未达到阈值',
    }
    temporary = output / "result.part"
    temporary.write_text(json.dumps(result, ensure_ascii=False))
    temporary.replace(output / "result.json")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if success else 1


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1]))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print("工作负载执行失败: " + type(exc).__name__, file=sys.stderr)
        sys.exit(1)

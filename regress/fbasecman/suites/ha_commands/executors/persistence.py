"""HA console command executors: PERSISTENCE."""

import time
import os
try:
    import fcntl
except ImportError:
    fcntl = None
import sys
import shlex
from pathlib import Path

from framework.configuration import load_regression_config
from suites.ha_commands.runtime import HaCommandFailure, HaCommandRuntime
from suites.ha_commands.helpers import *

__all__ = ['_run_application_name_persistence', '_run_backup_directory_permissions', '_run_backup_path_regular_file_rejected', '_run_backup_symlink_rejected', '_run_candidate_validation_rejected', '_run_config_backup_dir', '_run_crlf_format_preservation', '_run_duplicate_and_conflicting_weights', '_run_duplicate_object_rejected_after_start', '_run_eof_without_newline_preservation', '_run_external_edit_conflict', '_run_file_metadata_preservation', '_run_group_defaults_persistence', '_run_hash_inside_string_preservation', '_run_include_rejected_after_start', '_run_locked_disk_object_resolution', '_run_locked_invalid_numeric_token', '_run_readonly_config_directory', '_run_readonly_config_file', '_run_reload_failure_rollback', '_run_reload_restore_failure', '_run_rename_failure_protection', '_run_single_line_block_preservation', '_run_single_read_only_persistence', '_run_stable_lock_contention', '_run_stable_lock_directory_rejected', '_run_stable_lock_permissions', '_run_stable_lock_symlink_rejected', '_run_status_format_preservation', '_run_weight_format_preservation']


def _run_readonly_config_file(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    os.chmod(conf, 0o444)
    rt.psql('SHOW NODES;', "查看只读配置文件命令前的运行态",
            "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WEIGHT pg_3=11;',
                  "配置文件无写权限时拒绝持久化命令",
                  "返回 configuration file is not writable 和 Permission denied",
                  lambda output: ("configuration file" in output and "is not writable" in output))
    rt.assert_no_backup_created(backup, conf,
                                "验证只读配置文件命令未创建备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看只读配置文件拒绝后的运行态",
            "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    os.chmod(conf, 0o640)
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "恢复配置文件权限后修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证配置文件权限修复后的修改备份")
    rt.psql('SHOW NODES;', "查看配置文件权限修复后的运行态",
            "pg_3 weight 为 11",
            lambda output: _node_has_weight(output, 'pg_3', 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复只读配置文件用例权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证只读配置文件用例恢复备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看只读配置文件用例恢复后的运行态",
            "pg_3 weight 恢复为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_hash_inside_string_preservation(rt):
    conf = rt.start(transform=_add_hash_inside_string)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    marker = 'log_syslog_ident "fbase#inside-string"'
    rt.psql('SHOW NODES;', "查看字符串 # 保持命令前的节点权重",
            'pg_3 的 weight 为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "修改包含 # 字符串配置中的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证字符串 # 场景修改的配置备份")
    text = conf.read_text(encoding="utf-8")
    rt.check("验证字符串内部 # 未被当作注释",
             "log_syslog_ident 字符串逐字保持",
             "字符串存在=%s" % (marker in text), marker in text)
    rt.psql('SHOW NODES;', "查看字符串 # 场景修改后的运行态",
            'pg_3 的 weight 为 11',
            lambda output: _node_has_weight(output, "pg_3", 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复包含 # 字符串配置中的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证字符串 # 场景恢复的配置备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "验证字符串 # 场景恢复后的运行态",
            'pg_3 的 weight 恢复为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))


def _run_group_defaults_persistence(rt):
    conf = rt.start(transform=_omit_group_defaults)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    initial = conf.read_text(encoding="utf-8")
    rt.check("确认 group 默认字段测试前提",
             "balance/single 省略 access_mode，所有 check auto group 保留可连接 storage_db",
             "balance access omitted=%s；single access omitted=%s；rep storage retained=%s" % (
                 'access_mode' not in _datasource_block(initial.replace('group "',
                     'datasources "'), 'balance_group'),
                 'access_mode' not in _datasource_block(initial.replace('group "',
                     'datasources "'), 'single_group'),
                 'storage_db "postgres"' in _datasource_block(initial.replace('group "',
                     'datasources "'), 'rep_group')),
             all((
                 'access_mode' not in _datasource_block(initial.replace('group "',
                     'datasources "'), 'balance_group'),
                 'access_mode' not in _datasource_block(initial.replace('group "',
                     'datasources "'), 'single_group'),
                 'storage_db "postgres"' in _datasource_block(initial.replace('group "',
                     'datasources "'), 'rep_group'),
             )))
    for group, mode, access in (("balance_group", "balance", "READ_WRITE"),
                                ("single_group", "single", "READ_WRITE")):
        rt.psql('SHOW GROUP_ROUTING %s;' % group,
                "查看 %s 默认字段下命令前运行态" % group,
                "%s 使用默认 READ_WRITE 且存在 VALID 路由" % group,
                lambda output, group=group, mode=mode, access=access:
                all(v in output for v in (group, mode, "active")))
    rt.psql('SHOW NODES;', "查看默认字段场景修改前的节点运行态", "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "默认字段配置下修改节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证默认字段场景修改备份")
    changed = conf.read_text(encoding="utf-8")
    rt.check("验证默认字段未被写回物化",
             "省略的 access_mode/storage_db 仍保持省略",
             "除 weight 外配置结构保持=%s" %
             (changed.replace('    weight 11', '    weight 10', 1) == initial),
             changed.replace('    weight 11', '    weight 10', 1) == initial)
    rt.diff_contains(before, conf, ('-    weight 10', '+    weight 11'),
                     "验证默认字段场景的权重配置 diff")
    rt.psql('SHOW NODES;', "查看默认字段场景修改后的节点运行态", "pg_3 weight 为 11",
            lambda output: _node_has_weight(output, 'pg_3', 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复默认字段配置下的节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证默认字段场景恢复备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看默认字段场景恢复后的节点运行态", "pg_3 weight 恢复为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_stable_lock_symlink_rejected(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    lock_path = rt.workdir / 'conf-backup' / (conf.name + '.lock')
    if lock_path.exists() or lock_path.is_symlink():
        lock_path.unlink()
    lock_target = rt.workdir / "lock-target"
    lock_target.write_text("must remain unchanged\n", encoding="utf-8")
    target_before = lock_target.read_bytes()
    lock_path.symlink_to(lock_target.name)
    rt.psql('SHOW NODES;', "查看锁符号链接命令前的运行态",
            "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WEIGHT pg_3=11;',
                  "拒绝通过稳定锁符号链接更新配置",
                  "返回 cannot lock 和 symbolic link 系统错误",
                  lambda output: "cannot lock configuration" in output)
    rt.assert_no_backup_created(backup, conf,
                                "验证锁符号链接命令未创建备份")
    rt.diff(before, conf)
    rt.check("验证稳定锁链接目标未被修改",
             "lock-target 内容逐字节不变",
             "target unchanged=%s" % (lock_target.read_bytes() == target_before),
             lock_target.read_bytes() == target_before)
    rt.psql('SHOW NODES;', "查看锁符号链接拒绝后的运行态",
            "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    lock_path.unlink()
    lock_path.touch(mode=0o600)
    os.chmod(lock_path, 0o600)
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "恢复普通锁文件后修改节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证恢复普通锁后的配置备份")
    rt.psql('SHOW NODES;', "查看普通锁恢复后修改的运行态",
            "pg_3 weight 为 11",
            lambda output: _node_has_weight(output, 'pg_3', 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复锁符号链接用例节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证锁符号链接用例恢复备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看锁符号链接用例恢复后的运行态",
            "pg_3 weight 恢复为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_file_metadata_preservation(rt):
    conf = rt.start()
    os.chmod(conf, 0o640)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    initial_stat = conf.stat()
    rt.psql('SHOW NODES;', "查看文件元数据场景命令前的运行态",
            "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "修改 0640 配置文件中的节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证文件元数据场景修改备份")
    created = sorted(set(path.name for path in (rt.workdir / 'conf-backup').iterdir()
                         if '.bak.' in path.name) -
                     set(backup.files))
    config_stat = conf.stat()
    backup_stat = (rt.workdir / 'conf-backup' / created[0]).stat()
    expected_mode = stat.S_IMODE(initial_stat.st_mode)
    metadata_ok = all((
        stat.S_IMODE(config_stat.st_mode) == expected_mode,
        stat.S_IMODE(backup_stat.st_mode) == expected_mode,
        config_stat.st_uid == initial_stat.st_uid,
        config_stat.st_gid == initial_stat.st_gid,
        backup_stat.st_uid == initial_stat.st_uid,
        backup_stat.st_gid == initial_stat.st_gid,
    ))
    rt.check("验证正式配置和备份的权限属主",
             "正式配置与备份均保持 mode 0640、原 uid/gid",
             "config=%04o uid=%d gid=%d；backup=%04o uid=%d gid=%d" % (
                 stat.S_IMODE(config_stat.st_mode), config_stat.st_uid, config_stat.st_gid,
                 stat.S_IMODE(backup_stat.st_mode), backup_stat.st_uid, backup_stat.st_gid),
             metadata_ok)
    rt.diff_contains(before, conf, ('-    weight 10', '+    weight 11'),
                     "验证文件元数据场景配置 diff")
    rt.psql('SHOW NODES;', "查看文件元数据场景命令后的运行态",
            "pg_3 weight 为 11",
            lambda output: _node_has_weight(output, 'pg_3', 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复 0640 配置文件中的节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证文件元数据场景恢复备份")
    rt.diff(before, conf)
    restored_stat = conf.stat()
    rt.check("验证恢复后正式配置元数据",
             "恢复后仍为 mode 0640、原 uid/gid",
             "mode=%04o uid=%d gid=%d" %
             (stat.S_IMODE(restored_stat.st_mode), restored_stat.st_uid, restored_stat.st_gid),
             stat.S_IMODE(restored_stat.st_mode) == expected_mode and
             restored_stat.st_uid == initial_stat.st_uid and
             restored_stat.st_gid == initial_stat.st_gid)
    rt.psql('SHOW NODES;', "查看文件元数据场景恢复后的运行态",
            "pg_3 weight 恢复为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_duplicate_and_conflicting_weights(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW NODES;', "查看重复同值命令前的节点权重",
            'pg_3 的 weight 为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql(
        'SET NODE WEIGHT pg_3=11,pg_3=11;',
        "执行重复同值 WEIGHT 命令",
        '返回 SET NODE 且命令不报错',
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    rt.assert_backup_created(backup, conf, "验证重复同值 WEIGHT 的配置备份")
    rt.diff_contains(before, conf, ('-    weight 10', '+    weight 11'),
                     "验证重复同值只产生一次配置修改")
    rt.psql('SHOW NODES;', "查看重复同值命令后的节点权重",
            'pg_3 的 weight 为 11',
            lambda output: _node_has_weight(output, "pg_3", 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复 pg_3 初始权重",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证恢复 WEIGHT 的配置备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看冲突 WEIGHT 命令前的节点权重",
            'pg_3 的 weight 已恢复为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error(
        'SET NODE WEIGHT pg_3=11,pg_3=12;',
        "执行同一 datasource 不同 weight 的冲突命令",
        '返回 ERROR，包含 pg_3 和 conflicting weights',
        lambda output: "ERROR:" in output and "pg_3" in output and "conflicting weights" in output,
    )
    rt.assert_no_backup_created(backup, conf, "验证冲突 WEIGHT 未创建备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "验证冲突 WEIGHT 命令后的节点权重",
            'pg_3 的 weight 仍为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))


def _run_eof_without_newline_preservation(rt):
    conf = rt.start(transform=_without_final_newline)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.psql('SHOW NODES;', "查看 EOF 格式修改前的节点权重",
            'pg_3 的 weight 为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "修改 EOF 无换行配置中的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 EOF 格式修改的配置备份")
    data = conf.read_bytes()
    rt.check("验证修改后 EOF 仍无换行",
             "配置最后一个字节不是 CR 或 LF",
             "结尾字节=%r" % (data[-1:] if data else b""),
             bool(data) and not data.endswith((b"\n", b"\r")))
    rt.psql('SHOW NODES;', "查看 EOF 格式修改后的运行态",
            'pg_3 的 weight 为 11',
            lambda output: _node_has_weight(output, "pg_3", 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复 EOF 无换行配置中的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 EOF 格式恢复的配置备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "验证 EOF 格式恢复后的运行态",
            'pg_3 的 weight 恢复为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))


def _run_candidate_validation_rejected(rt):
    conf = rt.start()
    _inject_after_start(
        conf, 'not_a_real_parameter "candidate validation must reject this"')
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.psql('SHOW NODES;', "查看候选校验失败命令前的运行态",
            "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WEIGHT pg_3=11;',
                  "验证非法候选配置拒绝高可用命令",
                  "返回 candidate configuration is invalid 且未持久化",
                  lambda output: ("candidate configuration" in output and
                                  "is invalid" in output and
                                  "not persisted" in output))
    rt.assert_no_backup_created(backup, conf,
                                "验证候选校验失败未创建备份")
    rt.diff(before, conf)
    temp_files = sorted(path.name for path in rt.workdir.iterdir()
                        if '.tmp.' in path.name)
    rt.check("验证候选临时文件已清理",
             "workdir 中不遗留候选 .tmp 文件",
             "candidate temp files=%s" % temp_files,
             not temp_files)
    rt.psql('SHOW NODES;', "查看候选校验失败命令后的运行态",
            "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_config_backup_dir(rt):
    dir_explicit = rt.workdir / "backup-explicit"
    dir_reload = rt.workdir / "backup-reload"
    default_dir = rt.workdir / "conf-backup"

    def with_explicit_dir(content):
        return 'config_backup_dir "%s"\n' % dir_explicit + content

    conf = rt.start(transform=with_explicit_dir)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())

    def set_backup_dir_line(new_line, title):
        text = conf.read_text(encoding="utf-8")
        pattern = r'(?m)^config_backup_dir[^\n]*\n'
        if re.search(pattern, text):
            new_text = re.sub(pattern, new_line, text, count=1)
        else:
            new_text = new_line + text
        conf.write_text(new_text, encoding="utf-8")
        rt.record_step(
            title, "编辑配置文件 %s" % conf.name,
            "config_backup_dir 行更新为: %s" % (new_line.strip() or "(配置项已删除)"),
            "配置已写入磁盘", "PASS")

    def check_backup_file(directory, name, title):
        path = directory / name
        conf_stat = conf.stat()
        file_stat = path.stat() if path.exists() else None
        expected_mode = stat.S_IMODE(conf_stat.st_mode)
        actual_mode = stat.S_IMODE(file_stat.st_mode) if file_stat else 0
        rt.check(
            title,
            "备份文件以 %s.bak. 前缀命名且权限属主继承正式配置 (mode=%04o)" %
            (conf.name, expected_mode),
            "备份文件=%s mode=%04o uid=%s gid=%s" % (
                name, actual_mode,
                file_stat.st_uid if file_stat else "-",
                file_stat.st_gid if file_stat else "-"),
            path.is_file() and actual_mode == expected_mode
            and file_stat.st_uid == conf_stat.st_uid
            and file_stat.st_gid == conf_stat.st_gid
            and name.startswith(conf.name + ".bak."))

    startup_log = rt.proxy_log.read_text(encoding="utf-8", errors="replace")
    matched_dir_lines = [
        line.strip() for line in startup_log.splitlines()
        if "config_backup_dir" in line and str(dir_explicit) in line
    ]
    rt.check(
        "验证启动日志记录显式备份目录",
        "配置打印包含 config_backup_dir 指向 %s" % dir_explicit,
        "匹配行=%s" % (matched_dir_lines[0] if matched_dir_lines else "<未找到>"),
        bool(matched_dir_lines))
    dir_mode = stat.S_IMODE(dir_explicit.stat().st_mode) if dir_explicit.exists() else 0
    rt.check(
        "验证显式备份目录自动创建",
        "目录 %s 存在且权限为 0700" % dir_explicit,
        "目录存在=%s 权限=%04o" % (dir_explicit.is_dir(), dir_mode),
        dir_explicit.is_dir() and dir_mode == 0o700)
    rt.check(
        "验证默认备份目录未创建",
        "配置项显式指定后不再创建默认 conf-backup",
        "conf-backup 存在=%s" % default_dir.exists(),
        not default_dir.exists())
    rt.psql('SHOW NODES;', "查看修改前的节点权重",
            "pg_3 的初始 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup_explicit = rt.backup_checkpoint(conf, backup_dir=dir_explicit)
    backup_default = rt.backup_checkpoint(conf, backup_dir=default_dir)
    rt.psql('SET NODE WEIGHT pg_3=11;', "显式备份目录下修改 pg_3 权重为 11",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    name = rt.assert_backup_created(
        backup_explicit, conf, "验证备份写入显式目录 backup-explicit")
    check_backup_file(dir_explicit, name, "验证显式目录备份文件命名和权限")
    rt.assert_no_backup_created(
        backup_default, conf, "验证默认 conf-backup 未产生备份")
    rt.psql('SHOW NODES;', "查看显式目录用例修改后的运行态",
            "pg_3 weight 为 11",
            lambda output: _node_has_weight(output, 'pg_3', 11))
    backup_explicit = rt.backup_checkpoint(conf, backup_dir=dir_explicit)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复显式目录用例权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup_explicit, conf, "验证显式目录下的恢复备份")

    set_backup_dir_line('config_backup_dir "%s"\n' % dir_reload,
                        "修改配置文件：config_backup_dir 指向 backup-reload")
    rt.psql('RELOAD;', "Reload 应用新的备份目录",
            "返回 RELOAD 且不报错",
            lambda output: "RELOAD" in output and "ERROR" not in output)
    backup_reload = rt.backup_checkpoint(conf, backup_dir=dir_reload)
    backup_explicit = rt.backup_checkpoint(conf, backup_dir=dir_explicit)
    backup_default = rt.backup_checkpoint(conf, backup_dir=default_dir)
    rt.psql('SET NODE WEIGHT pg_4=11;', "Reload 后修改 pg_4 权重为 11",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    name = rt.assert_backup_created(
        backup_reload, conf, "验证 Reload 生效：备份写入新目录 backup-reload")
    check_backup_file(dir_reload, name, "验证新目录备份文件命名和权限")
    rt.assert_no_backup_created(
        backup_explicit, conf, "验证旧目录 backup-explicit 不再新增备份")
    rt.assert_no_backup_created(
        backup_default, conf, "验证默认 conf-backup 仍未产生备份")
    backup_reload = rt.backup_checkpoint(conf, backup_dir=dir_reload)
    rt.psql('SET NODE WEIGHT pg_4=10;', "恢复 pg_4 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup_reload, conf, "验证新目录下的恢复备份")

    blocked = rt.workdir / "backup-blocked-file"
    blocked.write_text("occupies the configured backup path\n", encoding="utf-8")
    set_backup_dir_line('config_backup_dir "%s"\n' % blocked,
                        "修改配置文件：config_backup_dir 指向普通文件")
    rt.psql_error('RELOAD;', "Reload 到普通文件备份目录被拒绝",
                  "返回 config backup directory is not writable",
                  lambda output: "config backup directory is not writable" in output)
    backup_reload = rt.backup_checkpoint(conf, backup_dir=dir_reload)
    rt.psql('SET NODE WEIGHT pg_4=11;', "被拒绝的 Reload 后修改 pg_4 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(
        backup_reload, conf, "验证被拒绝的 Reload 未切换运行态目录")
    backup_reload = rt.backup_checkpoint(conf, backup_dir=dir_reload)
    rt.psql('SET NODE WEIGHT pg_4=10;', "恢复 pg_4 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup_reload, conf, "验证恢复备份仍落 backup-reload")

    set_backup_dir_line("", "修改配置文件：删除 config_backup_dir 配置项")
    rt.psql('RELOAD;', "Reload 回落到默认备份目录",
            "返回 RELOAD 且不报错",
            lambda output: "RELOAD" in output and "ERROR" not in output)
    backup_default = rt.backup_checkpoint(conf, backup_dir=default_dir)
    backup_reload = rt.backup_checkpoint(conf, backup_dir=dir_reload)
    rt.psql('SET NODE WEIGHT pg_3=12;', "删除配置项后修改 pg_3 权重为 12",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    name = rt.assert_backup_created(
        backup_default, conf, "验证备份回落默认 conf-backup 目录")
    check_backup_file(default_dir, name, "验证默认目录备份文件命名和权限")
    rt.assert_no_backup_created(
        backup_reload, conf, "验证上一个显式目录不再新增备份")
    backup_default = rt.backup_checkpoint(conf, backup_dir=default_dir)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复 pg_3 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup_default, conf, "验证默认目录下的恢复备份")

    os.chmod(default_dir, 0o500)
    rt.record_step(
        "将生效中的 conf-backup 改为只读权限", "chmod 0500 %s" % default_dir,
        "备份目录后续无法创建备份文件", "mode=0500", "PASS")
    backup_default = rt.backup_checkpoint(conf, backup_dir=default_dir)
    try:
        rt.psql_error('SET NODE WEIGHT pg_3=11;',
                      "备份目录不可写时拒绝配置更新",
                      "返回 cannot create configuration backup 且权限拒绝",
                      lambda output: "ERROR:" in output and
                      "cannot create configuration backup" in output)
    finally:
        os.chmod(default_dir, 0o700)
    rt.assert_no_backup_created(
        backup_default, conf, "验证不可写目录下未产生备份")
    leftovers = sorted(path.name for path in rt.workdir.iterdir()
                       if path.name.endswith(".tmp"))
    rt.check("验证备份失败后无临时文件残留",
             "workdir 中不存在 .tmp 候选文件",
             "残留文件=%s" % (leftovers or "无"),
             not leftovers)
    rt.record_step(
        "恢复 conf-backup 目录权限", "chmod 0700 %s" % default_dir,
        "备份目录恢复可写", "mode=0700", "PASS")
    backup_default = rt.backup_checkpoint(conf, backup_dir=default_dir)
    rt.psql('SET NODE WEIGHT pg_3=11;', "恢复目录权限后修改 pg_3 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup_default, conf, "验证权限恢复后的修改备份")
    backup_default = rt.backup_checkpoint(conf, backup_dir=default_dir)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复 pg_3 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup_default, conf, "验证权限恢复后的恢复备份")

    set_backup_dir_line('config_backup_dir "%s"\n' % dir_explicit,
                        "恢复配置文件：config_backup_dir 指回 backup-explicit")
    rt.psql('RELOAD;', "Reload 恢复初始备份目录配置",
            "返回 RELOAD 且不报错",
            lambda output: "RELOAD" in output and "ERROR" not in output)
    backup_explicit = rt.backup_checkpoint(conf, backup_dir=dir_explicit)
    rt.psql('SET NODE WEIGHT pg_4=12;', "恢复配置后修改 pg_4 权重为 12",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(
        backup_explicit, conf, "验证显式目录恢复后再次生效")
    backup_explicit = rt.backup_checkpoint(conf, backup_dir=dir_explicit)
    rt.psql('SET NODE WEIGHT pg_4=10;', "恢复 pg_4 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup_explicit, conf, "验证恢复备份写入显式目录")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看用例结束时的节点权重",
            "pg_3 和 pg_4 weight 均恢复为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10)
            and _node_has_weight(output, 'pg_4', 10))

    rt.record_step(
        "停止 fbasecman 准备启动期校验", "停止当前实例",
        "进程停止后重新以非法 config_backup_dir 启动", "stopped", "PASS")
    rt.stop()
    rt.start_rejected(
        lambda content: 'config_backup_dir "%s"\n' % blocked + content,
        "非法 config_backup_dir 启动拒绝",
        "启动被拒绝且输出记录 config backup directory is not writable",
        lambda actual: ("start failed" in actual or "console not ready" in actual)
        and "config backup directory is not writable" in actual)


def _run_reload_restore_failure(rt):
    hook_source = rt.root / "suites" / "ha_commands" / "assets" / "rename_fault.c"
    hook_library = rt.workdir / "rename_fault.so"
    rt.run_command(
        ["cc", "-shared", "-fPIC", "-O2", "-o", str(hook_library),
         str(hook_source), "-ldl"],
        rt.logs_dir / "compile_reload_restore_fault.log",
        step_title="编译 Reload/restore 双重故障 hook")
    hook_log = rt.workdir / "reload_restore_fault.log"
    env = dict(os.environ)
    env.update({
        "LD_PRELOAD": str(hook_library),
        "FB_TEST_RENAME_TARGET": rt.case.name + ".conf",
        "FB_TEST_RENAME_LOG": str(hook_log),
        "FB_TEST_RENAME_MODE": "reload-restore-failure",
    })
    conf = rt.start(env=env)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.psql(
        'SET NODE WEIGHT pg_3=11;',
        "结构化发布直接使用已校验候选，不重新 Reload",
        "返回 SET NODE",
        lambda output: "SET NODE" in output and "ERROR" not in output)
    hook_text = hook_log.read_text(encoding="utf-8", errors="replace") if hook_log.exists() else ""
    proxy_text = rt.proxy_log.read_text(encoding="utf-8", errors="replace") if rt.proxy_log.exists() else ""
    rt.check(
        "验证结构化发布不受 rename 后候选污染影响",
        "hook 命中候选污染但内存候选成功发布",
        "hook:\n%s\n\nproxy matches=%s" %
        (hook_text, "config restore failed" in proxy_text),
        "candidate corrupted after validation" in hook_text)
    runtime_state = rt._record_ha_state(
        'SET NODE WEIGHT pg_3=11;', "restore 失败后 console 状态")
    rt.check(
        "验证结构化发布后的运行态",
        "pg_3 运行态 weight 为 11",
        "runtime weight is 11=%s" % _node_has_weight(runtime_state, 'pg_3', 11),
        _node_has_weight(runtime_state, 'pg_3', 11))
    conf.write_bytes(before.read_bytes())
    rt.psql('RELOAD;', "测试清理：恢复初始配置并重新 Reload",
            "返回 RELOAD，运行态与磁盘重新一致",
            lambda output: "RELOAD" in output and "ERROR" not in output)
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "验证故障清理后的运行态",
            "pg_3 weight 为初始值 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_locked_disk_object_resolution(rt):
    conf = rt.start(transform=_rename_disk_datasource)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.psql('SHOW DATASOURCES;', "查看磁盘对象重命名前运行态", "pg_3_disk 为 active replica",
            lambda output: all(v in output for v in ("pg_3_disk", "active")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3_disk=11;', "按加锁后磁盘对象名称修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证磁盘对象名称修改备份")
    rt.diff_contains(before, conf, ('-    weight 10', '+    weight 11'), "验证磁盘对象名称权重 diff")
    rt.psql('SHOW DATASOURCES;', "查看磁盘对象名称修改后的运行态",
            "pg_3_disk 仍为 active replica",
            lambda output: all(v in output for v in ("pg_3_disk", "active")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3_disk=10;', "恢复磁盘对象名称节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证磁盘对象名称恢复备份")
    rt.diff(before, conf)
    rt.psql('SHOW DATASOURCES;', "查看磁盘对象名称恢复后的运行态",
            "pg_3_disk 恢复为 active replica",
            lambda output: all(v in output for v in ("pg_3_disk", "active")))


def _run_backup_symlink_rejected(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    target = rt.workdir / "backup-target"
    _remove_test_path(target)
    target.mkdir()
    backup_dir = rt.workdir / "conf-backup"
    _remove_test_path(backup_dir)
    backup_dir.symlink_to(target.name, target_is_directory=True)
    rt.psql('SHOW NODES;', "查看备份目录符号链接命令前的运行态",
            "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WEIGHT pg_3=11;',
                  "拒绝通过 conf-backup 符号链接创建备份",
                  "返回配置更新失败及安全相关系统错误",
                  lambda output: "ERROR:" in output and
                  ("configuration path" in output or
                   "cannot create configuration backup" in output))
    rt.assert_no_backup_created(backup, conf,
                                "验证符号链接目标未新增备份")
    rt.diff(before, conf)
    target_files = sorted(path.name for path in target.iterdir())
    rt.check("验证未跟随备份目录符号链接",
             "backup-target 保持为空",
             "backup-target files=%s" % target_files,
             not target_files)
    rt.psql('SHOW NODES;', "查看备份目录符号链接拒绝后的运行态",
            "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_readonly_config_directory(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.psql('SHOW NODES;', "查看只读配置目录命令前的运行态",
            "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    os.chmod(rt.workdir, 0o500)
    try:
        rt.psql_error('SET NODE WEIGHT pg_3=11;',
                      "配置父目录不可写时拒绝持久化命令",
                      "返回配置更新失败和 Permission denied",
                      lambda output: "ERROR:" in output and
                      ("cannot create temporary configuration file" in output or
                       "cannot lock configuration" in output))
    finally:
        os.chmod(rt.workdir, 0o700)
    rt.assert_no_backup_created(backup, conf,
                                "验证只读配置目录命令未创建备份")
    rt.diff(before, conf)
    temp_files = sorted(path.name for path in rt.workdir.iterdir()
                        if '.tmp.' in path.name)
    rt.check("验证只读目录失败后无候选临时文件",
             "workdir 中不遗留 .tmp 文件",
             "candidate temp files=%s" % temp_files,
             not temp_files)
    rt.psql('SHOW NODES;', "查看只读配置目录拒绝后的运行态",
            "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "恢复配置目录权限后修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证配置目录修复后的修改备份")
    rt.psql('SHOW NODES;', "查看配置目录修复后的运行态",
            "pg_3 weight 为 11",
            lambda output: _node_has_weight(output, 'pg_3', 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复只读配置目录用例权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证只读配置目录用例恢复备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看只读配置目录用例恢复后的运行态",
            "pg_3 weight 恢复为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_crlf_format_preservation(rt):
    conf = rt.start(transform=_as_crlf)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.psql('SHOW NODES;', "查看 CRLF 配置修改前的节点权重",
            'pg_3 的 weight 为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "修改 CRLF 配置中的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 CRLF 修改的配置备份")
    data = conf.read_bytes()
    rt.check("验证修改后保持 CRLF 换行",
             "配置包含 CRLF 且不存在裸 LF",
             "CRLF数量=%d；裸LF数量=%d" %
             (data.count(b"\r\n"), data.replace(b"\r\n", b"").count(b"\n")),
             _has_only_crlf(data))
    rt.psql('SHOW NODES;', "查看 CRLF 配置修改后的运行态",
            'pg_3 的 weight 为 11',
            lambda output: _node_has_weight(output, "pg_3", 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复 CRLF 配置中的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 CRLF 恢复的配置备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "验证 CRLF 配置恢复后的运行态",
            'pg_3 的 weight 恢复为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))


def _run_stable_lock_permissions(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    lock_path = rt.workdir / 'conf-backup' / (conf.name + '.lock')
    lock_path.touch(mode=0o600, exist_ok=True)
    os.chmod(lock_path, 0o666)
    rt.psql('SHOW NODES;', "查看不安全锁权限命令前的运行态",
            "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WEIGHT pg_3=11;',
                  "拒绝使用 other-writable 的稳定锁",
                  "返回 cannot lock 和 Operation not permitted",
                  lambda output: "cannot lock configuration" in output)
    rt.assert_no_backup_created(backup, conf,
                                "验证不安全锁权限命令未创建备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看不安全锁权限拒绝后的运行态",
            "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    os.chmod(lock_path, 0o600)
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "修复稳定锁权限后修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证修复锁权限后的修改备份")
    rt.psql('SHOW NODES;', "查看修复稳定锁权限后的运行态",
            "pg_3 weight 为 11",
            lambda output: _node_has_weight(output, 'pg_3', 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复稳定锁权限用例权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证稳定锁权限用例恢复备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看稳定锁权限用例恢复后的运行态",
            "pg_3 weight 恢复为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_application_name_persistence(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    initial = conf.read_text(encoding="utf-8")
    rt.check("确认 application_name 测试前提",
             "pg_1 未配置 application_name，pg_3 显式配置 pg_240",
             "pg_1 default=%s；pg_3 explicit=%s" %
             ('application_name' not in _datasource_block(initial, 'pg_1'),
              'application_name "pg_240"' in _datasource_block(initial, 'pg_3')),
             'application_name' not in _datasource_block(initial, 'pg_1') and
             'application_name "pg_240"' in _datasource_block(initial, 'pg_3'))
    rt.psql('SHOW NODES;', "查看 application_name 场景修改前的运行态",
            "pg_1、pg_3 weight 均为 10",
            lambda output: (_node_has_weight(output, 'pg_1', 10) and
                            _node_has_weight(output, 'pg_3', 10)))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_1=11,pg_3=11;',
            "同时修改默认和显式 application_name 节点",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 application_name 场景修改备份")
    changed = conf.read_text(encoding="utf-8")
    rt.check("验证 application_name 写回保持",
             "pg_1 仍无 application_name，pg_3 仍为 pg_240",
             "pg_1 default=%s；pg_3 explicit=%s" %
             ('application_name' not in _datasource_block(changed, 'pg_1'),
              'application_name "pg_240"' in _datasource_block(changed, 'pg_3')),
             'application_name' not in _datasource_block(changed, 'pg_1') and
             'application_name "pg_240"' in _datasource_block(changed, 'pg_3'))
    rt.diff_contains(before, conf, ('+    weight 11',),
                     "验证 application_name 场景仅修改权重")
    rt.psql('SHOW NODES;', "查看 application_name 场景修改后的运行态",
            "pg_1、pg_3 weight 均为 11",
            lambda output: (_node_has_weight(output, 'pg_1', 11) and
                            _node_has_weight(output, 'pg_3', 11)))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_1=10,pg_3=10;',
            "恢复默认和显式 application_name 节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 application_name 场景恢复备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看 application_name 场景恢复后的运行态",
            "pg_1、pg_3 weight 均恢复为 10",
            lambda output: (_node_has_weight(output, 'pg_1', 10) and
                            _node_has_weight(output, 'pg_3', 10)))


def _run_rename_failure_protection(rt):
    hook_source = rt.root / "suites" / "ha_commands" / "assets" / "rename_fault.c"
    hook_library = rt.workdir / "rename_fault.so"
    rt.run_command(
        ["cc", "-shared", "-fPIC", "-O2", "-o", str(hook_library),
         str(hook_source), "-ldl"],
        rt.logs_dir / "compile_rename_fault.log",
        step_title="编译限定路径的 renameat fault hook")
    hook_log = rt.workdir / "rename_fault.log"
    env = dict(os.environ)
    env.update({
        "LD_PRELOAD": str(hook_library),
        "FB_TEST_RENAME_TARGET": rt.case.name + ".conf",
        "FB_TEST_RENAME_LOG": str(hook_log),
    })
    conf = rt.start(env=env)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.psql('SHOW NODES;', "查看 rename 故障命令前的运行态",
            "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WEIGHT pg_3=11;',
                  "候选文件 renameat 返回 EIO 时拒绝提交",
                  "返回配置更新失败和 Input/output error",
                  lambda output: "ERROR:" in output and "cannot save configuration" in output)
    rt.assert_backup_created(backup, conf,
                             "验证 rename 失败前已创建正确备份")
    rt.diff(before, conf)
    temp_files = sorted(path.name for path in rt.workdir.iterdir()
                        if '.tmp.' in path.name)
    hook_hit = hook_log.exists() and "candidate rejected" in hook_log.read_text(
        encoding="utf-8", errors="replace")
    rt.check("验证 rename fault hook 命中且候选已清理",
             "hook 命中一次以上且 workdir 无候选 .tmp 文件",
             "hook_hit=%s candidate_temp_files=%s" % (hook_hit, temp_files),
             hook_hit and not temp_files)
    rt.psql('SHOW NODES;', "查看 rename 失败命令后的运行态",
            "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_duplicate_object_rejected_after_start(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    text = conf.read_text(encoding="utf-8")
    start = text.index('datasources "pg_3" {')
    end = text.index('\n}\n', start) + 3
    conf.write_text(text + "\n" + text[start:end] + "\n", encoding="utf-8")
    before.write_bytes(conf.read_bytes())
    rt.psql('SHOW NODES;', "查看同名 datasource 注入前的运行态", "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, "pg_3", 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WEIGHT pg_3=11;', "拒绝包含同名 datasource 的配置写入",
                  '返回重复对象配置错误',
                  lambda output: "ERROR:" in output and "invalid or ambiguous" in output)
    rt.assert_no_backup_created(backup, conf)
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看同名对象拒绝后的运行态", "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, "pg_3", 10))


def _run_stable_lock_contention(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.psql('SHOW NODES;', "查看稳定锁占用命令前的运行态",
            "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    lock_path = rt.workdir / 'conf-backup' / (conf.name + '.lock')
    lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    if fcntl is not None:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    backup = rt.backup_checkpoint(conf)
    started = time.monotonic()
    try:
        rt.psql_error('SET NODE WEIGHT pg_3=11;',
                      "稳定锁被占用时拒绝配置更新",
                      "约 5 秒内返回 already in progress 和 try again",
                      lambda output: ("already in progress" in output and
                                      "try again" in output))
    finally:
        elapsed = time.monotonic() - started
        if fcntl is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    rt.check("验证稳定锁等待时间受限",
             "等待时间在 4 至 7 秒之间",
             "elapsed_seconds=%.3f" % elapsed,
             4.0 <= elapsed <= 7.0)
    rt.assert_no_backup_created(backup, conf,
                                "验证锁冲突命令未创建备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看稳定锁冲突后的运行态",
            "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "锁释放后修改节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证锁释放后修改的配置备份")
    rt.psql('SHOW NODES;', "查看锁释放后修改的运行态",
            "pg_3 weight 为 11",
            lambda output: _node_has_weight(output, 'pg_3', 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复稳定锁用例节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证稳定锁用例恢复的配置备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看稳定锁用例恢复后的运行态",
            "pg_3 weight 恢复为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_include_rejected_after_start(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    _inject_after_start(conf, 'include "%s";' % (rt.workdir / "included.conf"))
    before.write_bytes(conf.read_bytes())
    (rt.workdir / "included.conf").write_text("# injected include\n", encoding="utf-8")
    rt.psql('SHOW NODES;', "查看 include 注入前的运行态", "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, "pg_3", 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WEIGHT pg_3=11;', "拒绝包含 include 的配置写入",
                  '返回 include directives and cannot be updated 错误',
                  lambda output: "include" in output and "cannot be updated" in output)
    rt.assert_no_backup_created(backup, conf)
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看 include 拒绝后的运行态", "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, "pg_3", 10))


def _run_status_format_preservation(rt):
    conf = rt.start(transform=_status_with_format)
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW DATASOURCES;', "查看格式保持 PARTED 前的运行态",
            'pg_3 为 active',
            lambda output: "pg_3" in output and "active" in output)
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE PARTED pg_3;', "修改带特殊格式的 status 为 PARTED",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 status PARTED 的配置备份")
    text = conf.read_text(encoding="utf-8")
    parted_line = '\tstatus    "parted"    # keep-status-format'
    rt.check("验证 status PARTED 周边格式保持",
             "保留 tab 缩进、多个空格和行尾注释",
             "格式行存在=%s" % (parted_line in text), parted_line in text)
    rt.psql('SHOW DATASOURCES;', "查看格式保持 PARTED 后的运行态",
            'pg_3 为 parted',
            lambda output: "pg_3" in output and "parted" in output)
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE ACTIVE pg_3;', "恢复带特殊格式的 status 为 ACTIVE",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 status ACTIVE 的配置备份")
    rt.diff(before, conf)
    _wait_pg_cluster_ready(
        rt, "pg_cluster_1", "pg_1", ("pg_3",),
        "验证格式保持 ACTIVE 后 monitor 投影恢复")


def _run_single_read_only_persistence(rt):
    conf = rt.start(transform=_single_read_only)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    standby_port = str(rt.env.config["database"]["ports"]["mmr1_standby1"])
    rt.psql('SHOW GROUP_ROUTING single_group;', "查看 single read_only 命令前运行态",
            "single_group 为 single，候选 pg_3 为 active",
            lambda output: all(v in output for v in
                               ("single_group", "single", "pg_3", "replica", "active")))
    rt.psql_business(
        'SELECT inet_server_addr(), inet_server_port(), pg_is_in_recovery();',
        "验证 single read_only 修改前的真实读路由",
        "连接落到 pg_3 备库且 pg_is_in_recovery 为 true",
        lambda output: standby_port in output and " t" in output,
        group="single_group")
    rt.psql('SHOW NODES;', "查看 single read_only 修改前的节点权重", "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "single read_only 配置下修改备库权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 single read_only 修改备份")
    rt.diff_contains(before, conf, ('-    weight 10', '+    weight 11'),
                     "验证 single read_only 场景权重配置 diff")
    rt.psql('SHOW GROUP_ROUTING single_group;', "查看 single read_only 命令后运行态",
            "single_group 仍为 READ_ONLY，pg_3 仍为 active replica",
            lambda output: all(v in output for v in
                               ("single_group", "pg_3", "replica", "active")))
    rt.psql_business_error(
        'CREATE TEMP TABLE ha_single_ro_test(i int);',
        "验证 single read_only 拒绝业务写 SQL",
        "返回 cannot execute CREATE TABLE in a read-only transaction",
        lambda output: "cannot execute CREATE TABLE in a read-only transaction" in output,
        group="single_group")
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复 single read_only 备库权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证 single read_only 恢复备份")
    rt.diff(before, conf)
    rt.psql('SHOW GROUP_ROUTING single_group;', "查看 single read_only 恢复后运行态",
            "single_group 仍为 READ_ONLY，pg_3 为 active replica",
            lambda output: all(v in output for v in
                               ("single_group", "pg_3", "replica", "active")))


def _run_reload_failure_rollback(rt):
    conf = rt.start(transform=_single_read_only)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.record_step(
        "说明用例 18 当前复测场景",
        "目标命令: SET NODE PARTED pg_3;",
        ("single_group 是 READ_ONLY single 组，pg_3 是当前唯一 active replica；"
         "当前代码允许无可用 replica，PARTED 和恢复命令都应成功"),
        "先后验证 SHOW、配置备份、配置 diff 和恢复后的运行态",
        "PASS",
    )
    rt.psql('SHOW DATASOURCES;', "查看唯一只读副本 PARTED 前的 datasource 状态",
            "pg_3 为 active replica",
            lambda output: all(v in output for v in
                               ("pg_3", "active")))
    rt.psql('SHOW GROUP_ROUTING single_group;', "查看唯一只读副本 PARTED 前的路由",
            "single_group 为 VALID READ_ONLY，候选为 pg_3 replica",
            lambda output: all(v in output for v in
                               ("single_group", "active", "pg_3")))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE PARTED pg_3;', "PARTED single_group 当前唯一只读副本 pg_3",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证唯一只读副本 PARTED 的配置备份")
    rt.diff_contains(before, conf, ('-    status "active"', '+    status "parted"'),
                     "验证 pg_3 PARTED 配置 diff")
    rt.psql('SHOW DATASOURCES;', "查看唯一只读副本 PARTED 后的状态",
            "pg_3 为 parted replica",
            lambda output: all(v in output for v in
                               ("pg_3", "parted")))
    rt.psql('SHOW GROUP_ROUTING single_group;', "查看唯一只读副本 PARTED 后的路由",
            "single_group 为 READ_ONLY，且无 replica 时回退到 pg_1 primary",
            lambda output: ("single_group" in output and "pg_1" in output and "primary" in output
                            and "candidate_node" in output))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE ACTIVE pg_3;', "恢复 single_group 唯一只读副本 pg_3",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证唯一只读副本 ACTIVE 恢复的配置备份")
    rt.diff(before, conf)
    _wait_pg_cluster_ready(
        rt, "pg_cluster_1", "pg_1", ("pg_3",),
        "验证唯一只读副本 ACTIVE 后恢复可信 replica 投影")


def _run_locked_invalid_numeric_token(rt):
    conf = rt.start()
    text = conf.read_text(encoding="utf-8")
    marker = 'datasources "pg_3" {'
    start = text.index(marker)
    weight = text.index('    weight 10', start)
    conf.write_text(text[:weight] + '    weight 10abc' + text[weight + len('    weight 10'):], encoding="utf-8")
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.psql('SHOW NODES;', "查看非法数字 token 命令前的运行态", "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WEIGHT pg_3=10;',
                  "拒绝锁内解析的 10abc 非法数字 token",
                  "返回 configuration objects invalid or ambiguous",
                  lambda output: "configuration objects" in output and "invalid or ambiguous" in output)
    rt.assert_no_backup_created(backup, conf, "验证非法数字 token 未创建备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看非法数字 token 拒绝后的运行态", "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_external_edit_conflict(rt):
    hook_source = rt.root / "suites" / "ha_commands" / "assets" / "rename_fault.c"
    hook_library = rt.workdir / "rename_fault.so"
    rt.run_command(
        ["cc", "-shared", "-fPIC", "-O2", "-o", str(hook_library),
         str(hook_source), "-ldl"],
        rt.logs_dir / "compile_external_edit_hook.log",
        step_title="编译外部编辑冲突 fault hook")
    hook_log = rt.workdir / "external_edit_hook.log"
    conf_path = rt.workdir / (rt.case.name + ".conf")
    env = dict(os.environ)
    env.update({
        "LD_PRELOAD": str(hook_library),
        "FB_TEST_RENAME_TARGET": rt.case.name + ".conf",
        "FB_TEST_RENAME_LOG": str(hook_log),
        "FB_TEST_RENAME_MODE": "external-edit",
        "FB_TEST_EXTERNAL_EDIT_PATH": str(conf_path),
    })
    conf = rt.start(env=env)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    rt.psql('SHOW NODES;', "查看外部编辑冲突命令前的运行态",
            "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WEIGHT pg_3=11;',
                  "外部编辑发生时拒绝覆盖正式配置",
                  "返回 configuration file changed during the command",
                  lambda output: ("changed during the command" in output and
                                  "no changes were persisted" in output),
                  compare_config=False)
    rt.assert_backup_created(backup, conf,
                             "验证外部编辑冲突前已创建命令前备份")
    external = conf.read_text(encoding="utf-8")
    rt.check("验证外部编辑被保留且候选未覆盖",
             "正式配置保留 hook 注释，pg_3 weight 未变为 11",
             "external_comment=%s weight11=%s" %
             ("external edit injected by hook" in external,
              '    weight 11' in external),
             "external edit injected by hook" in external and
             '    weight 11' not in external)
    rt.diff_contains(before, conf,
                     ('+# external edit injected by hook',),
                     "验证配置 diff 仅包含外部编辑")
    rt.psql('SHOW NODES;', "查看外部编辑冲突后的运行态",
            "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_weight_format_preservation(rt):
    conf = rt.start(transform=_weight_with_format)
    before = rt.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    rt.psql('SHOW NODES;', "查看格式保持命令前的节点权重",
            'pg_3 的 weight 为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "修改带特殊格式的 weight 字段",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证格式保持修改的配置备份")
    text = conf.read_text(encoding="utf-8")
    rt.check("验证 weight 字段周边格式保持",
             "保留 tab 缩进、多个空格和 # keep-weight-format 注释",
             "格式行存在=%s" % ('\tweight    11    # keep-weight-format' in text),
             '\tweight    11    # keep-weight-format' in text)
    rt.psql('SHOW NODES;', "查看格式保持修改后的运行态",
            'pg_3 的 weight 为 11',
            lambda output: _node_has_weight(output, "pg_3", 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复带特殊格式的 weight 字段",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证格式保持恢复的配置备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "验证格式保持恢复后的运行态",
            'pg_3 的 weight 恢复为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))


def _run_single_line_block_preservation(rt):
    conf = rt.start(transform=_pg3_as_single_line_block)
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    initial_line = next(line for line in conf.read_text(encoding="utf-8").splitlines()
                        if line.startswith('datasources "pg_3" {'))
    rt.psql('SHOW NODES;', "查看单行 block 修改前的节点权重",
            'pg_3 的 weight 为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "修改单行 datasource block 的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证单行 block 修改的配置备份")
    text = conf.read_text(encoding="utf-8")
    changed_line = next((line for line in text.splitlines()
                         if line.startswith('datasources "pg_3" {')), "")
    expected_line = initial_line.replace("weight 10", "weight 11", 1)
    rt.check("验证 datasource block 保持单行且仅替换 weight",
             "修改后整行等于初始行仅将 weight 10 替换为 weight 11",
             "单行完全匹配=%s" % (changed_line == expected_line),
             changed_line == expected_line)
    rt.psql('SHOW NODES;', "查看单行 block 修改后的运行态",
            'pg_3 的 weight 为 11',
            lambda output: _node_has_weight(output, "pg_3", 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复单行 datasource block 的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证单行 block 恢复的配置备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "验证单行 block 恢复后的运行态",
            'pg_3 的 weight 恢复为 10',
            lambda output: _node_has_weight(output, "pg_3", 10))


def _run_backup_path_regular_file_rejected(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    backup_path = rt.workdir / "conf-backup"
    sentinel = b"must remain a regular file\n"
    _remove_test_path(backup_path)
    backup_path.write_bytes(sentinel)
    rt.psql('SHOW NODES;', "查看备份路径普通文件命令前的运行态",
            "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    rt.psql_error('SET NODE WEIGHT pg_3=11;',
                  "conf-backup 为普通文件时拒绝配置更新",
                  "返回配置更新失败和 Not a directory",
                  lambda output: "ERROR:" in output and
                  ("configuration path" in output or
                   "cannot create configuration backup" in output))
    rt.diff(before, conf)
    rt.check("验证备份路径占位文件未被替换",
             "conf-backup 仍为普通文件且内容逐字节不变",
             "is_file=%s content_unchanged=%s" %
             (backup_path.is_file(), backup_path.read_bytes() == sentinel),
             backup_path.is_file() and backup_path.read_bytes() == sentinel)
    rt.psql('SHOW NODES;', "查看备份路径普通文件拒绝后的运行态",
            "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup_path.unlink()
    backup_path.mkdir(mode=0o700)
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "恢复安全备份目录后修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证备份路径修复后的修改备份")
    rt.psql('SHOW NODES;', "查看备份路径修复后的运行态",
            "pg_3 weight 为 11",
            lambda output: _node_has_weight(output, 'pg_3', 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复备份路径类型用例权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证备份路径类型用例恢复备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看备份路径类型用例恢复后的运行态",
            "pg_3 weight 恢复为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_backup_directory_permissions(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    backup_dir = rt.workdir / "conf-backup"
    _remove_test_path(backup_dir)
    backup_dir.mkdir(mode=0o700)
    os.chmod(backup_dir, 0o777)
    rt.psql('SHOW NODES;', "查看不安全备份目录命令前的运行态",
            "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "使用可写的 conf-backup 目录修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf, "验证可写备份目录下的修改备份")
    rt.psql('SHOW NODES;', "查看可写备份目录后的运行态",
            "pg_3 weight 为 11",
            lambda output: _node_has_weight(output, 'pg_3', 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复备份目录权限用例权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证备份目录权限用例恢复备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看备份目录权限用例恢复后的运行态",
            "pg_3 weight 恢复为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))


def _run_stable_lock_directory_rejected(rt):
    conf = rt.start()
    before = rt.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    lock_path = rt.workdir / 'conf-backup' / (conf.name + '.lock')
    if lock_path.exists() or lock_path.is_symlink():
        lock_path.unlink()
    lock_path.mkdir(mode=0o700)
    rt.psql('SHOW NODES;', "查看锁目录命令前的运行态",
            "pg_3 weight 为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    backup = rt.backup_checkpoint(conf)
    rt.psql_error('SET NODE WEIGHT pg_3=11;',
                  "稳定锁为目录时拒绝配置更新",
                  "返回 cannot lock 和 Is a directory",
                  lambda output: "cannot lock configuration" in output)
    rt.assert_no_backup_created(backup, conf,
                                "验证锁目录命令未创建备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看锁目录拒绝后的运行态",
            "pg_3 weight 仍为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))
    lock_path.rmdir()
    lock_path.touch(mode=0o600)
    os.chmod(lock_path, 0o600)
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=11;', "恢复普通锁文件后修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证锁目录修复后的修改备份")
    rt.psql('SHOW NODES;', "查看锁目录修复后的运行态",
            "pg_3 weight 为 11",
            lambda output: _node_has_weight(output, 'pg_3', 11))
    backup = rt.backup_checkpoint(conf)
    rt.psql('SET NODE WEIGHT pg_3=10;', "恢复锁目录用例节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    rt.assert_backup_created(backup, conf,
                             "验证锁目录用例恢复备份")
    rt.diff(before, conf)
    rt.psql('SHOW NODES;', "查看锁目录用例恢复后的运行态",
            "pg_3 weight 恢复为 10",
            lambda output: _node_has_weight(output, 'pg_3', 10))



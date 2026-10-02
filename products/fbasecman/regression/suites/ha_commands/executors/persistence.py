"""HA console command executors: PERSISTENCE."""

import time
import os
import stat
import re
import hashlib
try:
    import fcntl
except ImportError:
    fcntl = None

from suites.ha_commands.helpers import *
from platform_regress.clients.psql import parse_psql_table
__all__ = ['_run_application_name_persistence', '_run_backup_directory_permissions', '_run_backup_path_regular_file_rejected', '_run_backup_symlink_rejected', '_run_candidate_validation_rejected', '_run_config_backup_dir', '_run_crlf_format_preservation', '_run_duplicate_and_conflicting_weights', '_run_duplicate_object_rejected_after_start', '_run_eof_without_newline_preservation', '_run_external_edit_conflict', '_run_file_metadata_preservation', '_run_group_defaults_persistence', '_run_hash_inside_string_preservation', '_run_include_rejected_after_start', '_run_locked_disk_object_resolution', '_run_locked_invalid_numeric_token', '_run_readonly_config_directory', '_run_readonly_config_file', '_run_reload_failure_rollback', '_run_reload_restore_failure', '_run_rename_failure_protection', '_run_single_line_block_preservation', '_run_single_read_only_persistence', '_run_stable_lock_contention', '_run_stable_lock_directory_rejected', '_run_stable_lock_permissions', '_run_stable_lock_symlink_rejected', '_run_status_format_preservation', '_run_weight_format_preservation']


def _run_readonly_config_file(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    os.chmod(conf, 0o444)
    ops.assert_nodes("查看只读配置文件命令前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql_error('SET NODE WEIGHT pg_3=11;',
                  "配置文件无写权限时拒绝持久化命令",
                  "返回 configuration file is not writable 和 Permission denied",
                  lambda output: ("configuration file" in output and "is not writable" in output))
    ops.assert_no_backup_created(backup, conf,
                                "验证只读配置文件命令未创建备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看只读配置文件拒绝后的运行态", {"pg_3": {"weight": "10"}})
    os.chmod(conf, 0o640)
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "恢复配置文件权限后修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证配置文件权限修复后的修改备份")
    ops.assert_nodes("查看配置文件权限修复后的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复只读配置文件用例权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证只读配置文件用例恢复备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看只读配置文件用例恢复后的运行态", {"pg_3": {"weight": "10"}})


def _run_hash_inside_string_preservation(context):
    ops = context.ops
    conf = ops.start(transform=_add_hash_inside_string)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    marker = 'log_syslog_ident "fbase#inside-string"'
    ops.assert_nodes("查看字符串 # 保持命令前的节点权重", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "修改包含 # 字符串配置中的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证字符串 # 场景修改的配置备份")
    text = conf.read_text(encoding="utf-8")
    ops.check("验证字符串内部 # 未被当作注释",
             "log_syslog_ident 字符串逐字保持",
             "命中行: %s" % _matching_line(text, 'log_syslog_ident'),
             marker in text)
    ops.assert_nodes("查看字符串 # 场景修改后的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复包含 # 字符串配置中的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证字符串 # 场景恢复的配置备份")
    ops.diff(before, conf)
    ops.assert_nodes("验证字符串 # 场景恢复后的运行态", {"pg_3": {"weight": "10"}})


def _run_group_defaults_persistence(context):
    ops = context.ops
    conf = ops.start(transform=_omit_group_defaults)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    initial = conf.read_text(encoding="utf-8")
    balance_block = _datasource_block(
        initial.replace('group "', 'datasources "'), 'balance_group')
    single_block = _datasource_block(
        initial.replace('group "', 'datasources "'), 'single_group')
    rep_block = _datasource_block(
        initial.replace('group "', 'datasources "'), 'rep_group')
    ops.check("确认 group 默认字段测试前提",
             "balance/single 省略 access_mode，所有 check auto group 保留可连接 storage_db",
             "balance_group: %r\n      single_group: %r\n      rep_group.storage_db: %r" % (
                 balance_block, single_block,
                 _matching_line(rep_block, 'storage_db')),
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
        ops.psql('SHOW GROUP_ROUTING %s;' % group,
                "查看 %s 默认字段下命令前运行态" % group,
                "%s 使用默认 READ_WRITE 且存在 VALID 路由" % group,
                lambda output, group=group, mode=mode, access=access:
                all(v in output for v in (group, mode, "active")))
    ops.assert_nodes("查看默认字段场景修改前的节点运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "默认字段配置下修改节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证默认字段场景修改备份")
    changed = conf.read_text(encoding="utf-8")
    restored_text = changed.replace('    weight 11', '    weight 10', 1)
    first_diff = next(
        ("第 %d 行\n      期望: %r\n      实际: %r" % (i + 1, e, a)
         for i, (e, a) in enumerate(zip(initial.splitlines(),
                                      restored_text.splitlines()))
         if e != a), "无差异")
    ops.check("验证默认字段未被写回物化",
             "省略的 access_mode/storage_db 仍保持省略",
             "首处差异: %s" % first_diff if restored_text != initial else
             "除 weight 11 回写外全文逐行一致",
             restored_text == initial)
    ops.diff_contains(before, conf, ('-    weight 10', '+    weight 11'),
                     "验证默认字段场景的权重配置 diff")
    ops.assert_nodes("查看默认字段场景修改后的节点运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复默认字段配置下的节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证默认字段场景恢复备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看默认字段场景恢复后的节点运行态", {"pg_3": {"weight": "10"}})


def _run_stable_lock_symlink_rejected(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    lock_path = ops.workdir / 'conf-backup' / (conf.name + '.lock')
    if lock_path.exists() or lock_path.is_symlink():
        lock_path.unlink()
    lock_target = ops.workdir / "lock-target"
    lock_target.write_text("must remain unchanged\n", encoding="utf-8")
    target_before = lock_target.read_bytes()
    lock_path.symlink_to(lock_target.name)
    ops.assert_nodes("查看锁符号链接命令前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql_error('SET NODE WEIGHT pg_3=11;',
                  "拒绝通过稳定锁符号链接更新配置",
                  "返回 cannot lock 和 symbolic link 系统错误",
                  lambda output: "cannot lock configuration" in output)
    ops.assert_no_backup_created(backup, conf,
                                "验证锁符号链接命令未创建备份")
    ops.diff(before, conf)
    ops.check("验证稳定锁链接目标未被修改",
             "lock-target 内容逐字节不变",
             "目标大小=%d 字节；sha256=%s；与操作前一致=%s" % (
                 len(lock_target.read_bytes()),
                 hashlib.sha256(lock_target.read_bytes()).hexdigest()[:16],
                 lock_target.read_bytes() == target_before),
             lock_target.read_bytes() == target_before)
    ops.assert_nodes("查看锁符号链接拒绝后的运行态", {"pg_3": {"weight": "10"}})
    lock_path.unlink()
    lock_path.touch(mode=0o600)
    os.chmod(lock_path, 0o600)
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "恢复普通锁文件后修改节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证恢复普通锁后的配置备份")
    ops.assert_nodes("查看普通锁恢复后修改的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复锁符号链接用例节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证锁符号链接用例恢复备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看锁符号链接用例恢复后的运行态", {"pg_3": {"weight": "10"}})


def _run_file_metadata_preservation(context):
    ops = context.ops
    conf = ops.start()
    os.chmod(conf, 0o640)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    initial_stat = conf.stat()
    ops.assert_nodes("查看文件元数据场景命令前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "修改 0640 配置文件中的节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证文件元数据场景修改备份")
    created = sorted(set(path.name for path in (ops.workdir / 'conf-backup').iterdir()
                         if '.bak.' in path.name) -
                     set(backup.files))
    config_stat = conf.stat()
    backup_stat = (ops.workdir / 'conf-backup' / created[0]).stat()
    expected_mode = stat.S_IMODE(initial_stat.st_mode)
    metadata_ok = all((
        stat.S_IMODE(config_stat.st_mode) == expected_mode,
        stat.S_IMODE(backup_stat.st_mode) == expected_mode,
        config_stat.st_uid == initial_stat.st_uid,
        config_stat.st_gid == initial_stat.st_gid,
        backup_stat.st_uid == initial_stat.st_uid,
        backup_stat.st_gid == initial_stat.st_gid,
    ))
    ops.check("验证正式配置和备份的权限属主",
             "正式配置与备份均保持 mode 0640、原 uid/gid",
             "config=%04o uid=%d gid=%d；backup=%04o uid=%d gid=%d" % (
                 stat.S_IMODE(config_stat.st_mode), config_stat.st_uid, config_stat.st_gid,
                 stat.S_IMODE(backup_stat.st_mode), backup_stat.st_uid, backup_stat.st_gid),
             metadata_ok)
    ops.diff_contains(before, conf, ('-    weight 10', '+    weight 11'),
                     "验证文件元数据场景配置 diff")
    ops.assert_nodes("查看文件元数据场景命令后的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复 0640 配置文件中的节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证文件元数据场景恢复备份")
    ops.diff(before, conf)
    restored_stat = conf.stat()
    ops.check("验证恢复后正式配置元数据",
             "恢复后仍为 mode 0640、原 uid/gid",
             "mode=%04o uid=%d gid=%d" %
             (stat.S_IMODE(restored_stat.st_mode), restored_stat.st_uid, restored_stat.st_gid),
             stat.S_IMODE(restored_stat.st_mode) == expected_mode and
             restored_stat.st_uid == initial_stat.st_uid and
             restored_stat.st_gid == initial_stat.st_gid)
    ops.assert_nodes("查看文件元数据场景恢复后的运行态", {"pg_3": {"weight": "10"}})


def _run_duplicate_and_conflicting_weights(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    ops.assert_nodes("查看重复同值命令前的节点权重", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql(
        'SET NODE WEIGHT pg_3=11,pg_3=11;',
        "执行重复同值 WEIGHT 命令",
        '返回 SET NODE 且命令不报错',
        lambda output: "SET NODE" in output and "ERROR" not in output,
    )
    ops.assert_backup_created(backup, conf, "验证重复同值 WEIGHT 的配置备份")
    ops.diff_contains(before, conf, ('-    weight 10', '+    weight 11'),
                     "验证重复同值只产生一次配置修改")
    ops.assert_nodes("查看重复同值命令后的节点权重", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复 pg_3 初始权重",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证恢复 WEIGHT 的配置备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看冲突 WEIGHT 命令前的节点权重", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql_error(
        'SET NODE WEIGHT pg_3=11,pg_3=12;',
        "执行同一 datasource 不同 weight 的冲突命令",
        '返回 ERROR，包含 pg_3 和 conflicting weights',
        lambda output: "ERROR:" in output and "pg_3" in output and "conflicting weights" in output,
    )
    ops.assert_no_backup_created(backup, conf, "验证冲突 WEIGHT 未创建备份")
    ops.diff(before, conf)
    ops.assert_nodes("验证冲突 WEIGHT 命令后的节点权重", {"pg_3": {"weight": "10"}})


def _run_eof_without_newline_preservation(context):
    ops = context.ops
    conf = ops.start(transform=_without_final_newline)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    ops.assert_nodes("查看 EOF 格式修改前的节点权重", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "修改 EOF 无换行配置中的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 EOF 格式修改的配置备份")
    data = conf.read_bytes()
    ops.check("验证修改后 EOF 仍无换行",
             "配置最后一个字节不是 CR 或 LF",
             "结尾字节=%r" % (data[-1:] if data else b""),
             bool(data) and not data.endswith((b"\n", b"\r")))
    ops.assert_nodes("查看 EOF 格式修改后的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复 EOF 无换行配置中的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 EOF 格式恢复的配置备份")
    ops.diff(before, conf)
    ops.assert_nodes("验证 EOF 格式恢复后的运行态", {"pg_3": {"weight": "10"}})


def _run_candidate_validation_rejected(context):
    ops = context.ops
    conf = ops.start()
    _inject_after_start(
        conf, 'not_a_real_parameter "candidate validation must reject this"')
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    ops.assert_nodes("查看候选校验失败命令前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql_error('SET NODE WEIGHT pg_3=11;',
                  "验证非法候选配置拒绝高可用命令",
                  "返回 candidate configuration is invalid 且未持久化",
                  lambda output: ("candidate configuration" in output and
                                  "is invalid" in output and
                                  "not persisted" in output))
    ops.assert_no_backup_created(backup, conf,
                                "验证候选校验失败未创建备份")
    ops.diff(before, conf)
    temp_files = sorted(path.name for path in ops.workdir.iterdir()
                        if '.tmp.' in path.name)
    ops.check("验证候选临时文件已清理",
             "workdir 中不遗留候选 .tmp 文件",
             "candidate temp files=%s" % temp_files,
             not temp_files)
    ops.assert_nodes("查看候选校验失败命令后的运行态", {"pg_3": {"weight": "10"}})


def _run_config_backup_dir(context):
    ops = context.ops
    dir_explicit = ops.workdir / "backup-explicit"
    dir_reload = ops.workdir / "backup-reload"
    default_dir = ops.workdir / "conf-backup"

    def with_explicit_dir(content):
        return 'config_backup_dir "%s"\n' % dir_explicit + content

    conf = ops.start(transform=with_explicit_dir)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())

    def set_backup_dir_line(new_line, title):
        text = conf.read_text(encoding="utf-8")
        pattern = r'(?m)^config_backup_dir[^\n]*\n'
        if re.search(pattern, text):
            new_text = re.sub(pattern, new_line, text, count=1)
        else:
            new_text = new_line + text
        conf.write_text(new_text, encoding="utf-8")
        ops.record_step(
            title, "编辑配置文件 %s" % conf.name,
            "config_backup_dir 行更新为: %s" % (new_line.strip() or "(配置项已删除)"),
            "配置已写入磁盘", "PASS")

    def check_backup_file(directory, name, title):
        path = directory / name
        conf_stat = conf.stat()
        file_stat = path.stat() if path.exists() else None
        expected_mode = stat.S_IMODE(conf_stat.st_mode)
        actual_mode = stat.S_IMODE(file_stat.st_mode) if file_stat else 0
        ops.check(
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

    startup_log = ops.proxy_log.read_text(encoding="utf-8", errors="replace")
    matched_dir_lines = [
        line.strip() for line in startup_log.splitlines()
        if "config_backup_dir" in line and str(dir_explicit) in line
    ]
    ops.check(
        "验证启动日志记录显式备份目录",
        "配置打印包含 config_backup_dir 指向 %s" % dir_explicit,
        "匹配行=%s" % (matched_dir_lines[0] if matched_dir_lines else "<未找到>"),
        bool(matched_dir_lines))
    dir_mode = stat.S_IMODE(dir_explicit.stat().st_mode) if dir_explicit.exists() else 0
    ops.check(
        "验证显式备份目录自动创建",
        "目录 %s 存在且权限为 0700" % dir_explicit,
        "目录存在=%s 权限=%04o" % (dir_explicit.is_dir(), dir_mode),
        dir_explicit.is_dir() and dir_mode == 0o700)
    ops.check(
        "验证默认备份目录未创建",
        "配置项显式指定后不再创建默认 conf-backup",
        "conf-backup 存在=%s；workdir 现有目录=%s" % (
            default_dir.exists(),
            sorted(p.name for p in ops.workdir.iterdir() if p.is_dir())),
        not default_dir.exists())
    ops.assert_nodes("查看修改前的节点权重", {"pg_3": {"weight": "10"}})
    backup_explicit = ops.backup_checkpoint(conf, backup_dir=dir_explicit)
    backup_default = ops.backup_checkpoint(conf, backup_dir=default_dir)
    ops.psql('SET NODE WEIGHT pg_3=11;', "显式备份目录下修改 pg_3 权重为 11",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    name = ops.assert_backup_created(
        backup_explicit, conf, "验证备份写入显式目录 backup-explicit")
    check_backup_file(dir_explicit, name, "验证显式目录备份文件命名和权限")
    ops.assert_no_backup_created(
        backup_default, conf, "验证默认 conf-backup 未产生备份")
    ops.assert_nodes("查看显式目录用例修改后的运行态", {"pg_3": {"weight": "11"}})
    backup_explicit = ops.backup_checkpoint(conf, backup_dir=dir_explicit)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复显式目录用例权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup_explicit, conf, "验证显式目录下的恢复备份")

    set_backup_dir_line('config_backup_dir "%s"\n' % dir_reload,
                        "修改配置文件：config_backup_dir 指向 backup-reload")
    ops.psql('RELOAD;', "Reload 应用新的备份目录",
            "返回 RELOAD 且不报错",
            lambda output: "RELOAD" in output and "ERROR" not in output)
    backup_reload = ops.backup_checkpoint(conf, backup_dir=dir_reload)
    backup_explicit = ops.backup_checkpoint(conf, backup_dir=dir_explicit)
    backup_default = ops.backup_checkpoint(conf, backup_dir=default_dir)
    ops.psql('SET NODE WEIGHT pg_4=11;', "Reload 后修改 pg_4 权重为 11",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    name = ops.assert_backup_created(
        backup_reload, conf, "验证 Reload 生效：备份写入新目录 backup-reload")
    check_backup_file(dir_reload, name, "验证新目录备份文件命名和权限")
    ops.assert_no_backup_created(
        backup_explicit, conf, "验证旧目录 backup-explicit 不再新增备份")
    ops.assert_no_backup_created(
        backup_default, conf, "验证默认 conf-backup 仍未产生备份")
    backup_reload = ops.backup_checkpoint(conf, backup_dir=dir_reload)
    ops.psql('SET NODE WEIGHT pg_4=10;', "恢复 pg_4 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup_reload, conf, "验证新目录下的恢复备份")

    blocked = ops.workdir / "backup-blocked-file"
    blocked.write_text("occupies the configured backup path\n", encoding="utf-8")
    set_backup_dir_line('config_backup_dir "%s"\n' % blocked,
                        "修改配置文件：config_backup_dir 指向普通文件")
    ops.psql_error('RELOAD;', "Reload 到普通文件备份目录被拒绝",
                  "返回 config backup directory is not writable",
                  lambda output: "config backup directory is not writable" in output)
    backup_reload = ops.backup_checkpoint(conf, backup_dir=dir_reload)
    ops.psql('SET NODE WEIGHT pg_4=11;', "被拒绝的 Reload 后修改 pg_4 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(
        backup_reload, conf, "验证被拒绝的 Reload 未切换运行态目录")
    backup_reload = ops.backup_checkpoint(conf, backup_dir=dir_reload)
    ops.psql('SET NODE WEIGHT pg_4=10;', "恢复 pg_4 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup_reload, conf, "验证恢复备份仍落 backup-reload")

    set_backup_dir_line("", "修改配置文件：删除 config_backup_dir 配置项")
    ops.psql('RELOAD;', "Reload 回落到默认备份目录",
            "返回 RELOAD 且不报错",
            lambda output: "RELOAD" in output and "ERROR" not in output)
    backup_default = ops.backup_checkpoint(conf, backup_dir=default_dir)
    backup_reload = ops.backup_checkpoint(conf, backup_dir=dir_reload)
    ops.psql('SET NODE WEIGHT pg_3=12;', "删除配置项后修改 pg_3 权重为 12",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    name = ops.assert_backup_created(
        backup_default, conf, "验证备份回落默认 conf-backup 目录")
    check_backup_file(default_dir, name, "验证默认目录备份文件命名和权限")
    ops.assert_no_backup_created(
        backup_reload, conf, "验证上一个显式目录不再新增备份")
    backup_default = ops.backup_checkpoint(conf, backup_dir=default_dir)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复 pg_3 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup_default, conf, "验证默认目录下的恢复备份")

    os.chmod(default_dir, 0o500)
    ops.record_step(
        "将生效中的 conf-backup 改为只读权限", "chmod 0500 %s" % default_dir,
        "备份目录后续无法创建备份文件", "mode=0500", "PASS")
    backup_default = ops.backup_checkpoint(conf, backup_dir=default_dir)
    try:
        ops.psql_error('SET NODE WEIGHT pg_3=11;',
                      "备份目录不可写时拒绝配置更新",
                      "返回 cannot create configuration backup 且权限拒绝",
                      lambda output: "ERROR:" in output and
                      "cannot create configuration backup" in output)
    finally:
        os.chmod(default_dir, 0o700)
    ops.assert_no_backup_created(
        backup_default, conf, "验证不可写目录下未产生备份")
    leftovers = sorted(path.name for path in ops.workdir.iterdir()
                       if path.name.endswith(".tmp"))
    ops.check("验证备份失败后无临时文件残留",
             "workdir 中不存在 .tmp 候选文件",
             "残留文件=%s" % (leftovers or "无"),
             not leftovers)
    ops.record_step(
        "恢复 conf-backup 目录权限", "chmod 0700 %s" % default_dir,
        "备份目录恢复可写", "mode=0700", "PASS")
    backup_default = ops.backup_checkpoint(conf, backup_dir=default_dir)
    ops.psql('SET NODE WEIGHT pg_3=11;', "恢复目录权限后修改 pg_3 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup_default, conf, "验证权限恢复后的修改备份")
    backup_default = ops.backup_checkpoint(conf, backup_dir=default_dir)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复 pg_3 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup_default, conf, "验证权限恢复后的恢复备份")

    set_backup_dir_line('config_backup_dir "%s"\n' % dir_explicit,
                        "恢复配置文件：config_backup_dir 指回 backup-explicit")
    ops.psql('RELOAD;', "Reload 恢复初始备份目录配置",
            "返回 RELOAD 且不报错",
            lambda output: "RELOAD" in output and "ERROR" not in output)
    backup_explicit = ops.backup_checkpoint(conf, backup_dir=dir_explicit)
    ops.psql('SET NODE WEIGHT pg_4=12;', "恢复配置后修改 pg_4 权重为 12",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(
        backup_explicit, conf, "验证显式目录恢复后再次生效")
    backup_explicit = ops.backup_checkpoint(conf, backup_dir=dir_explicit)
    ops.psql('SET NODE WEIGHT pg_4=10;', "恢复 pg_4 权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup_explicit, conf, "验证恢复备份写入显式目录")
    ops.diff(before, conf)
    ops.assert_nodes("查看用例结束时的节点权重",
                     {"pg_3": {"weight": "10"}, "pg_4": {"weight": "10"}})

    ops.record_step(
        "停止 fbasecman 准备启动期校验", "停止当前实例",
        "进程停止后重新以非法 config_backup_dir 启动", "stopped", "PASS")
    ops.stop()
    ops.start_rejected(
        lambda content: 'config_backup_dir "%s"\n' % blocked + content,
        "非法 config_backup_dir 启动拒绝",
        "启动被拒绝且输出记录 config backup directory is not writable",
        lambda actual: ("start failed" in actual or "console not ready" in actual)
        and "config backup directory is not writable" in actual)


def _run_reload_restore_failure(context):
    ops = context.ops
    hook_source = ops.root / "suites" / "ha_commands" / "assets" / "rename_fault.c"
    hook_library = ops.workdir / "rename_fault.so"
    ops.run_command(
        ["cc", "-shared", "-fPIC", "-O2", "-o", str(hook_library),
         str(hook_source), "-ldl"],
        ops.logs_dir / "compile_reload_restore_fault.log",
        step_title="编译 Reload/restore 双重故障 hook")
    hook_log = ops.workdir / "reload_restore_fault.log"
    env = dict(os.environ)
    env.update({
        "LD_PRELOAD": str(hook_library),
        "FB_TEST_RENAME_TARGET": ops.case.name + ".conf",
        "FB_TEST_RENAME_LOG": str(hook_log),
        "FB_TEST_RENAME_MODE": "reload-restore-failure",
    })
    conf = ops.start(env=env)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    ops.psql(
        'SET NODE WEIGHT pg_3=11;',
        "结构化发布直接使用已校验候选，不重新 Reload",
        "返回 SET NODE",
        lambda output: "SET NODE" in output and "ERROR" not in output)
    hook_text = hook_log.read_text(encoding="utf-8", errors="replace") if hook_log.exists() else ""
    proxy_text = ops.proxy_log.read_text(encoding="utf-8", errors="replace") if ops.proxy_log.exists() else ""
    ops.check(
        "验证结构化发布不受 rename 后候选污染影响",
        "hook 命中候选污染但内存候选成功发布",
        "hook:\n%s\n\nproxy matches=%s" %
        (hook_text, "config restore failed" in proxy_text),
        "candidate corrupted after validation" in hook_text)
    runtime_state = ops._record_ha_state(
        'SET NODE WEIGHT pg_3=11;', "restore 失败后 console 状态")
    runtime_rows = {r.get("node_name"): r
                    for r in parse_psql_table(runtime_state)}
    ops.check(
        "验证结构化发布后的运行态",
        "pg_3 运行态 weight 为 11",
        "pg_3 运行态行=%s" % runtime_rows.get("pg_3", "<缺失>"),
        runtime_rows.get("pg_3", {}).get("weight") == "11")
    conf.write_bytes(before.read_bytes())
    ops.psql('RELOAD;', "测试清理：恢复初始配置并重新 Reload",
            "返回 RELOAD，运行态与磁盘重新一致",
            lambda output: "RELOAD" in output and "ERROR" not in output)
    ops.diff(before, conf)
    ops.assert_nodes("验证故障清理后的运行态", {"pg_3": {"weight": "10"}})


def _run_locked_disk_object_resolution(context):
    ops = context.ops
    conf = ops.start(transform=_rename_disk_datasource)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    ops.assert_table(
        'SHOW DATASOURCES;', "查看磁盘对象重命名前运行态",
        {"pg_3_disk": {"config_status": "active"}},
        key="node_name")
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3_disk=11;', "按加锁后磁盘对象名称修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证磁盘对象名称修改备份")
    ops.diff_contains(before, conf, ('-    weight 10', '+    weight 11'), "验证磁盘对象名称权重 diff")
    ops.assert_table(
        'SHOW DATASOURCES;', "查看磁盘对象名称修改后的运行态",
        {"pg_3_disk": {"config_status": "active", "weight": "11"}},
        key="node_name")
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3_disk=10;', "恢复磁盘对象名称节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证磁盘对象名称恢复备份")
    ops.diff(before, conf)
    ops.assert_table(
        'SHOW DATASOURCES;', "查看磁盘对象名称恢复后的运行态",
        {"pg_3_disk": {"config_status": "active", "weight": "10"}},
        key="node_name")


def _run_backup_symlink_rejected(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    target = ops.workdir / "backup-target"
    _remove_test_path(target)
    target.mkdir()
    backup_dir = ops.workdir / "conf-backup"
    _remove_test_path(backup_dir)
    backup_dir.symlink_to(target.name, target_is_directory=True)
    ops.assert_nodes("查看备份目录符号链接命令前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql_error('SET NODE WEIGHT pg_3=11;',
                  "拒绝通过 conf-backup 符号链接创建备份",
                  "返回配置更新失败及安全相关系统错误",
                  lambda output: "ERROR:" in output and
                  ("configuration path" in output or
                   "cannot create configuration backup" in output))
    ops.assert_no_backup_created(backup, conf,
                                "验证符号链接目标未新增备份")
    ops.diff(before, conf)
    target_files = sorted(path.name for path in target.iterdir())
    ops.check("验证未跟随备份目录符号链接",
             "backup-target 保持为空",
             "backup-target files=%s" % target_files,
             not target_files)
    ops.assert_nodes("查看备份目录符号链接拒绝后的运行态", {"pg_3": {"weight": "10"}})


def _run_readonly_config_directory(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    ops.assert_nodes("查看只读配置目录命令前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    os.chmod(ops.workdir, 0o500)
    try:
        ops.psql_error('SET NODE WEIGHT pg_3=11;',
                      "配置父目录不可写时拒绝持久化命令",
                      "返回配置更新失败和 Permission denied",
                      lambda output: "ERROR:" in output and
                      ("cannot create temporary configuration file" in output or
                       "cannot lock configuration" in output))
    finally:
        os.chmod(ops.workdir, 0o700)
    ops.assert_no_backup_created(backup, conf,
                                "验证只读配置目录命令未创建备份")
    ops.diff(before, conf)
    temp_files = sorted(path.name for path in ops.workdir.iterdir()
                        if '.tmp.' in path.name)
    ops.check("验证只读目录失败后无候选临时文件",
             "workdir 中不遗留 .tmp 文件",
             "candidate temp files=%s" % temp_files,
             not temp_files)
    ops.assert_nodes("查看只读配置目录拒绝后的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "恢复配置目录权限后修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证配置目录修复后的修改备份")
    ops.assert_nodes("查看配置目录修复后的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复只读配置目录用例权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证只读配置目录用例恢复备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看只读配置目录用例恢复后的运行态", {"pg_3": {"weight": "10"}})


def _run_crlf_format_preservation(context):
    ops = context.ops
    conf = ops.start(transform=_as_crlf)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    ops.assert_nodes("查看 CRLF 配置修改前的节点权重", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "修改 CRLF 配置中的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 CRLF 修改的配置备份")
    data = conf.read_bytes()
    ops.check("验证修改后保持 CRLF 换行",
             "配置包含 CRLF 且不存在裸 LF",
             "CRLF数量=%d；裸LF数量=%d" %
             (data.count(b"\r\n"), data.replace(b"\r\n", b"").count(b"\n")),
             _has_only_crlf(data))
    ops.assert_nodes("查看 CRLF 配置修改后的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复 CRLF 配置中的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 CRLF 恢复的配置备份")
    ops.diff(before, conf)
    ops.assert_nodes("验证 CRLF 配置恢复后的运行态", {"pg_3": {"weight": "10"}})


def _run_stable_lock_permissions(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    lock_path = ops.workdir / 'conf-backup' / (conf.name + '.lock')
    lock_path.touch(mode=0o600, exist_ok=True)
    os.chmod(lock_path, 0o666)
    ops.assert_nodes("查看不安全锁权限命令前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql_error('SET NODE WEIGHT pg_3=11;',
                  "拒绝使用 other-writable 的稳定锁",
                  "返回 cannot lock 和 Operation not permitted",
                  lambda output: "cannot lock configuration" in output)
    ops.assert_no_backup_created(backup, conf,
                                "验证不安全锁权限命令未创建备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看不安全锁权限拒绝后的运行态", {"pg_3": {"weight": "10"}})
    os.chmod(lock_path, 0o600)
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "修复稳定锁权限后修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证修复锁权限后的修改备份")
    ops.assert_nodes("查看修复稳定锁权限后的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复稳定锁权限用例权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证稳定锁权限用例恢复备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看稳定锁权限用例恢复后的运行态", {"pg_3": {"weight": "10"}})


def _run_application_name_persistence(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    initial = conf.read_text(encoding="utf-8")
    ops.check("确认 application_name 测试前提",
             "pg_1 未配置 application_name，pg_3 显式配置 pg_240",
             "pg_1 block: %r\n      pg_3 application_name 行: %r" % (
                 _datasource_block(initial, 'pg_1'),
                 _matching_line(
                     _datasource_block(initial, 'pg_3'), 'application_name')),
             'application_name' not in _datasource_block(initial, 'pg_1') and
             'application_name "pg_240"' in _datasource_block(initial, 'pg_3'))
    ops.assert_nodes("查看 application_name 场景修改前的运行态",
                     {"pg_1": {"weight": "10"}, "pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_1=11,pg_3=11;',
            "同时修改默认和显式 application_name 节点",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 application_name 场景修改备份")
    changed = conf.read_text(encoding="utf-8")
    ops.check("验证 application_name 写回保持",
             "pg_1 仍无 application_name，pg_3 仍为 pg_240",
             "pg_1 block: %r\n      pg_3 application_name 行: %r" % (
                 _datasource_block(changed, 'pg_1'),
                 _matching_line(
                     _datasource_block(changed, 'pg_3'), 'application_name')),
             'application_name' not in _datasource_block(changed, 'pg_1') and
             'application_name "pg_240"' in _datasource_block(changed, 'pg_3'))
    ops.diff_contains(before, conf, ('+    weight 11',),
                     "验证 application_name 场景仅修改权重")
    ops.assert_nodes("查看 application_name 场景修改后的运行态",
                     {"pg_1": {"weight": "11"}, "pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_1=10,pg_3=10;',
            "恢复默认和显式 application_name 节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 application_name 场景恢复备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看 application_name 场景恢复后的运行态",
                     {"pg_1": {"weight": "10"}, "pg_3": {"weight": "10"}})


def _run_rename_failure_protection(context):
    ops = context.ops
    hook_source = ops.root / "suites" / "ha_commands" / "assets" / "rename_fault.c"
    hook_library = ops.workdir / "rename_fault.so"
    ops.run_command(
        ["cc", "-shared", "-fPIC", "-O2", "-o", str(hook_library),
         str(hook_source), "-ldl"],
        ops.logs_dir / "compile_rename_fault.log",
        step_title="编译限定路径的 renameat fault hook")
    hook_log = ops.workdir / "rename_fault.log"
    env = dict(os.environ)
    env.update({
        "LD_PRELOAD": str(hook_library),
        "FB_TEST_RENAME_TARGET": ops.case.name + ".conf",
        "FB_TEST_RENAME_LOG": str(hook_log),
    })
    conf = ops.start(env=env)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    ops.assert_nodes("查看 rename 故障命令前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql_error('SET NODE WEIGHT pg_3=11;',
                  "候选文件 renameat 返回 EIO 时拒绝提交",
                  "返回配置更新失败和 Input/output error",
                  lambda output: "ERROR:" in output and "cannot save configuration" in output)
    ops.assert_backup_created(backup, conf,
                             "验证 rename 失败前已创建正确备份")
    ops.diff(before, conf)
    temp_files = sorted(path.name for path in ops.workdir.iterdir()
                        if '.tmp.' in path.name)
    hook_hit = hook_log.exists() and "candidate rejected" in hook_log.read_text(
        encoding="utf-8", errors="replace")
    hook_lines = _matching_lines(
        hook_log.read_text(encoding="utf-8", errors="replace")
        if hook_log.exists() else "", "candidate rejected")
    ops.check("验证 rename fault hook 命中且候选已清理",
             "hook 命中一次以上且 workdir 无候选 .tmp 文件",
             "hook 命中行=%s；候选临时文件=%s" % (hook_lines, temp_files or "无"),
             hook_hit and not temp_files)
    ops.assert_nodes("查看 rename 失败命令后的运行态", {"pg_3": {"weight": "10"}})


def _run_duplicate_object_rejected_after_start(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    text = conf.read_text(encoding="utf-8")
    start = text.index('datasources "pg_3" {')
    end = text.index('\n}\n', start) + 3
    conf.write_text(text + "\n" + text[start:end] + "\n", encoding="utf-8")
    before.write_bytes(conf.read_bytes())
    ops.assert_nodes("查看同名 datasource 注入前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql_error('SET NODE WEIGHT pg_3=11;', "拒绝包含同名 datasource 的配置写入",
                  '返回重复对象配置错误',
                  lambda output: "ERROR:" in output and "invalid or ambiguous" in output)
    ops.assert_no_backup_created(backup, conf)
    ops.diff(before, conf)
    ops.assert_nodes("查看同名对象拒绝后的运行态", {"pg_3": {"weight": "10"}})


def _run_stable_lock_contention(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    ops.assert_nodes("查看稳定锁占用命令前的运行态", {"pg_3": {"weight": "10"}})
    lock_path = ops.workdir / 'conf-backup' / (conf.name + '.lock')
    lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    if fcntl is not None:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
    backup = ops.backup_checkpoint(conf)
    started = time.monotonic()
    try:
        ops.psql_error('SET NODE WEIGHT pg_3=11;',
                      "稳定锁被占用时拒绝配置更新",
                      "约 5 秒内返回 already in progress 和 try again",
                      lambda output: ("already in progress" in output and
                                      "try again" in output))
    finally:
        elapsed = time.monotonic() - started
        if fcntl is not None:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    ops.check("验证稳定锁等待时间受限",
             "等待时间在 4 至 7 秒之间",
             "elapsed_seconds=%.3f" % elapsed,
             4.0 <= elapsed <= 7.0)
    ops.assert_no_backup_created(backup, conf,
                                "验证锁冲突命令未创建备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看稳定锁冲突后的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "锁释放后修改节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证锁释放后修改的配置备份")
    ops.assert_nodes("查看锁释放后修改的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复稳定锁用例节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证稳定锁用例恢复的配置备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看稳定锁用例恢复后的运行态", {"pg_3": {"weight": "10"}})


def _run_include_rejected_after_start(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    _inject_after_start(conf, 'include "%s";' % (ops.workdir / "included.conf"))
    before.write_bytes(conf.read_bytes())
    (ops.workdir / "included.conf").write_text("# injected include\n", encoding="utf-8")
    ops.assert_nodes("查看 include 注入前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql_error('SET NODE WEIGHT pg_3=11;', "拒绝包含 include 的配置写入",
                  '返回 include directives and cannot be updated 错误',
                  lambda output: "include" in output and "cannot be updated" in output)
    ops.assert_no_backup_created(backup, conf)
    ops.diff(before, conf)
    ops.assert_nodes("查看 include 拒绝后的运行态", {"pg_3": {"weight": "10"}})


def _run_status_format_preservation(context):
    ops = context.ops
    conf = ops.start(transform=_status_with_format)
    before = ops.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    ops.assert_table(
        'SHOW DATASOURCES;', "查看格式保持 PARTED 前的运行态",
        {"pg_3": {"config_status": "active"}},
        key="node_name")
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE PARTED pg_3;', "修改带特殊格式的 status 为 PARTED",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 status PARTED 的配置备份")
    text = conf.read_text(encoding="utf-8")
    parted_line = '\tstatus    "parted"    # keep-status-format'
    ops.check("验证 status PARTED 周边格式保持",
             "保留 tab 缩进、多个空格和行尾注释",
             "期望=%r\n      命中=%r" % (
                 parted_line, _matching_line(text, 'status')),
             parted_line in text)
    ops.assert_table(
        'SHOW DATASOURCES;', "查看格式保持 PARTED 后的运行态",
        {"pg_3": {"config_status": "parted"}},
        key="node_name")
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE ACTIVE pg_3;', "恢复带特殊格式的 status 为 ACTIVE",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 status ACTIVE 的配置备份")
    ops.diff(before, conf)
    _wait_pg_cluster_ready(
        context, "pg_cluster_1", "pg_1", ("pg_3",),
        "验证格式保持 ACTIVE 后 monitor 投影恢复")


def _run_single_read_only_persistence(context):
    ops = context.ops
    conf = ops.start(transform=_single_read_only)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    standby_port = str(ops.env.config["database"]["ports"]["mmr1_standby1"])
    ops.assert_table(
        'SHOW GROUP_ROUTING single_group;',
        "查看 single read_only 命令前运行态",
        {"postgres|pg_3": {"group_mode": "single",
                           "effective_grouprole": "replica",
                           "effective_state": "active",
                           "route_status": "AVAILABLE"}},
        key=("user_name", "candidate_node"), retry_timeout=30)
    ops.assert_business_route(
        'SELECT inet_server_addr(), inet_server_port(), pg_is_in_recovery();',
        "验证 single read_only 修改前的真实读路由",
        port=standby_port, recovery=True,
        group="single_group", retry_timeout=30)
    ops.assert_nodes("查看 single read_only 修改前的节点权重", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "single read_only 配置下修改备库权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 single read_only 修改备份")
    ops.diff_contains(before, conf, ('-    weight 10', '+    weight 11'),
                     "验证 single read_only 场景权重配置 diff")
    ops.assert_table(
        'SHOW GROUP_ROUTING single_group;',
        "查看 single read_only 命令后运行态",
        {"postgres|pg_3": {"effective_grouprole": "replica",
                           "effective_state": "active",
                           "route_status": "AVAILABLE"}},
        key=("user_name", "candidate_node"), retry_timeout=30)
    ops.psql_business_error(
        'CREATE TEMP TABLE ha_single_ro_test(i int);',
        "验证 single read_only 拒绝业务写 SQL",
        "返回 cannot execute CREATE TABLE in a read-only transaction",
        lambda output: "cannot execute CREATE TABLE in a read-only transaction" in output,
        group="single_group")
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复 single read_only 备库权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证 single read_only 恢复备份")
    ops.diff(before, conf)
    ops.assert_table(
        'SHOW GROUP_ROUTING single_group;',
        "查看 single read_only 恢复后运行态",
        {"postgres|pg_3": {"effective_grouprole": "replica",
                           "effective_state": "active",
                           "route_status": "AVAILABLE"}},
        key=("user_name", "candidate_node"), retry_timeout=30)


def _run_reload_failure_rollback(context):
    ops = context.ops
    conf = ops.start(transform=_single_read_only)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    ops.record_step(
        "说明用例 18 当前复测场景",
        "目标命令: SET NODE PARTED pg_3;",
        ("single_group 是 READ_ONLY single 组，pg_3 是当前唯一 active replica；"
         "当前代码允许无可用 replica，PARTED 和恢复命令都应成功"),
        "先后验证 SHOW、配置备份、配置 diff 和恢复后的运行态",
        "PASS",
    )
    ops.assert_table(
        'SHOW DATASOURCES;', "查看唯一只读副本 PARTED 前的 datasource 状态",
        {"pg_3": {"config_status": "active"}},
        key="node_name")
    ops.assert_table(
        'SHOW GROUP_ROUTING single_group;',
        "查看唯一只读副本 PARTED 前的路由",
        {"postgres|pg_3": {"effective_grouprole": "replica",
                           "effective_state": "active",
                           "route_status": "AVAILABLE"}},
        key=("user_name", "candidate_node"), retry_timeout=30)
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE PARTED pg_3;', "PARTED single_group 当前唯一只读副本 pg_3",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证唯一只读副本 PARTED 的配置备份")
    ops.diff_contains(before, conf, ('-    status "active"', '+    status "parted"'),
                     "验证 pg_3 PARTED 配置 diff")
    ops.assert_table(
        'SHOW DATASOURCES;', "查看唯一只读副本 PARTED 后的状态",
        {"pg_3": {"config_status": "parted"}},
        key="node_name")
    ops.assert_table(
        'SHOW GROUP_ROUTING single_group;',
        "查看唯一只读副本 PARTED 后的路由（回退 primary）",
        {"postgres|pg_1": {"effective_grouprole": "primary",
                           "route_status": "AVAILABLE"}},
        key=("user_name", "candidate_node"), retry_timeout=30)
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE ACTIVE pg_3;', "恢复 single_group 唯一只读副本 pg_3",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证唯一只读副本 ACTIVE 恢复的配置备份")
    ops.diff(before, conf)
    _wait_pg_cluster_ready(
        context, "pg_cluster_1", "pg_1", ("pg_3",),
        "验证唯一只读副本 ACTIVE 后恢复可信 replica 投影")


def _run_locked_invalid_numeric_token(context):
    ops = context.ops
    conf = ops.start()
    text = conf.read_text(encoding="utf-8")
    marker = 'datasources "pg_3" {'
    start = text.index(marker)
    weight = text.index('    weight 10', start)
    conf.write_text(text[:weight] + '    weight 10abc' + text[weight + len('    weight 10'):], encoding="utf-8")
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    ops.assert_nodes("查看非法数字 token 命令前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql_error('SET NODE WEIGHT pg_3=10;',
                  "拒绝锁内解析的 10abc 非法数字 token",
                  "返回 configuration objects invalid or ambiguous",
                  lambda output: "configuration objects" in output and "invalid or ambiguous" in output)
    ops.assert_no_backup_created(backup, conf, "验证非法数字 token 未创建备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看非法数字 token 拒绝后的运行态", {"pg_3": {"weight": "10"}})


def _run_external_edit_conflict(context):
    ops = context.ops
    hook_source = ops.root / "suites" / "ha_commands" / "assets" / "rename_fault.c"
    hook_library = ops.workdir / "rename_fault.so"
    ops.run_command(
        ["cc", "-shared", "-fPIC", "-O2", "-o", str(hook_library),
         str(hook_source), "-ldl"],
        ops.logs_dir / "compile_external_edit_hook.log",
        step_title="编译外部编辑冲突 fault hook")
    hook_log = ops.workdir / "external_edit_hook.log"
    conf_path = ops.workdir / (ops.case.name + ".conf")
    env = dict(os.environ)
    env.update({
        "LD_PRELOAD": str(hook_library),
        "FB_TEST_RENAME_TARGET": ops.case.name + ".conf",
        "FB_TEST_RENAME_LOG": str(hook_log),
        "FB_TEST_RENAME_MODE": "external-edit",
        "FB_TEST_EXTERNAL_EDIT_PATH": str(conf_path),
    })
    conf = ops.start(env=env)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    ops.assert_nodes("查看外部编辑冲突命令前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql_error('SET NODE WEIGHT pg_3=11;',
                  "外部编辑发生时拒绝覆盖正式配置",
                  "返回 configuration file changed during the command",
                  lambda output: ("changed during the command" in output and
                                  "no changes were persisted" in output),
                  compare_config=False)
    ops.assert_backup_created(backup, conf,
                             "验证外部编辑冲突前已创建命令前备份")
    external = conf.read_text(encoding="utf-8")
    ops.check("验证外部编辑被保留且候选未覆盖",
             "正式配置保留 hook 注释，pg_3 weight 未变为 11",
             "hook 注释行=%r；weight 11 命中行=%s；pg_3 weight 行=%r" % (
                 _matching_line(external, "external edit injected by hook"),
                 _matching_lines(external, '    weight 11'),
                 _matching_line(
                     _datasource_block(external, 'pg_3'), 'weight')),
             "external edit injected by hook" in external and
             '    weight 11' not in external)
    ops.diff_contains(before, conf,
                     ('+# external edit injected by hook',),
                     "验证配置 diff 仅包含外部编辑")
    ops.assert_nodes("查看外部编辑冲突后的运行态", {"pg_3": {"weight": "10"}})


def _run_weight_format_preservation(context):
    ops = context.ops
    conf = ops.start(transform=_weight_with_format)
    before = ops.workdir / "before-command.conf"
    before.write_text(conf.read_text(encoding="utf-8"), encoding="utf-8")
    ops.assert_nodes("查看格式保持命令前的节点权重", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "修改带特殊格式的 weight 字段",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证格式保持修改的配置备份")
    text = conf.read_text(encoding="utf-8")
    ops.check("验证 weight 字段周边格式保持",
             "保留 tab 缩进、多个空格和 # keep-weight-format 注释",
             "期望=%r\n      命中=%r" % (
                 '\tweight    11    # keep-weight-format',
                 _matching_line(text, 'weight')),
             '\tweight    11    # keep-weight-format' in text)
    ops.assert_nodes("查看格式保持修改后的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复带特殊格式的 weight 字段",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证格式保持恢复的配置备份")
    ops.diff(before, conf)
    ops.assert_nodes("验证格式保持恢复后的运行态", {"pg_3": {"weight": "10"}})


def _run_single_line_block_preservation(context):
    ops = context.ops
    conf = ops.start(transform=_pg3_as_single_line_block)
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    initial_line = next(line for line in conf.read_text(encoding="utf-8").splitlines()
                        if line.startswith('datasources "pg_3" {'))
    ops.assert_nodes("查看单行 block 修改前的节点权重", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "修改单行 datasource block 的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证单行 block 修改的配置备份")
    text = conf.read_text(encoding="utf-8")
    changed_line = next((line for line in text.splitlines()
                         if line.startswith('datasources "pg_3" {')), "")
    expected_line = initial_line.replace("weight 10", "weight 11", 1)
    ops.check("验证 datasource block 保持单行且仅替换 weight",
             "修改后整行等于初始行仅将 weight 10 替换为 weight 11",
             "期望行=%r\n      实际行=%r" % (expected_line, changed_line),
             changed_line == expected_line)
    ops.assert_nodes("查看单行 block 修改后的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复单行 datasource block 的 weight",
            '返回 SET NODE 且命令不报错',
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证单行 block 恢复的配置备份")
    ops.diff(before, conf)
    ops.assert_nodes("验证单行 block 恢复后的运行态", {"pg_3": {"weight": "10"}})


def _run_backup_path_regular_file_rejected(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    backup_path = ops.workdir / "conf-backup"
    sentinel = b"must remain a regular file\n"
    _remove_test_path(backup_path)
    backup_path.write_bytes(sentinel)
    ops.assert_nodes("查看备份路径普通文件命令前的运行态", {"pg_3": {"weight": "10"}})
    ops.psql_error('SET NODE WEIGHT pg_3=11;',
                  "conf-backup 为普通文件时拒绝配置更新",
                  "返回配置更新失败和 Not a directory",
                  lambda output: "ERROR:" in output and
                  ("configuration path" in output or
                   "cannot create configuration backup" in output))
    ops.diff(before, conf)
    ops.check("验证备份路径占位文件未被替换",
             "conf-backup 仍为普通文件且内容逐字节不变",
             "is_file=%s；大小=%d 字节；内容=%r；与哨兵一致=%s" % (
                 backup_path.is_file(), backup_path.stat().st_size,
                 backup_path.read_bytes()[:40],
                 backup_path.read_bytes() == sentinel),
             backup_path.is_file() and backup_path.read_bytes() == sentinel)
    ops.assert_nodes("查看备份路径普通文件拒绝后的运行态", {"pg_3": {"weight": "10"}})
    backup_path.unlink()
    backup_path.mkdir(mode=0o700)
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "恢复安全备份目录后修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证备份路径修复后的修改备份")
    ops.assert_nodes("查看备份路径修复后的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复备份路径类型用例权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证备份路径类型用例恢复备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看备份路径类型用例恢复后的运行态", {"pg_3": {"weight": "10"}})


def _run_backup_directory_permissions(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    backup_dir = ops.workdir / "conf-backup"
    _remove_test_path(backup_dir)
    backup_dir.mkdir(mode=0o700)
    os.chmod(backup_dir, 0o777)
    ops.assert_nodes("查看不安全备份目录命令前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "使用可写的 conf-backup 目录修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf, "验证可写备份目录下的修改备份")
    ops.assert_nodes("查看可写备份目录后的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复备份目录权限用例权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证备份目录权限用例恢复备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看备份目录权限用例恢复后的运行态", {"pg_3": {"weight": "10"}})


def _run_stable_lock_directory_rejected(context):
    ops = context.ops
    conf = ops.start()
    before = ops.workdir / "before-command.conf"
    before.write_bytes(conf.read_bytes())
    lock_path = ops.workdir / 'conf-backup' / (conf.name + '.lock')
    if lock_path.exists() or lock_path.is_symlink():
        lock_path.unlink()
    lock_path.mkdir(mode=0o700)
    ops.assert_nodes("查看锁目录命令前的运行态", {"pg_3": {"weight": "10"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql_error('SET NODE WEIGHT pg_3=11;',
                  "稳定锁为目录时拒绝配置更新",
                  "返回 cannot lock 和 Is a directory",
                  lambda output: "cannot lock configuration" in output)
    ops.assert_no_backup_created(backup, conf,
                                "验证锁目录命令未创建备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看锁目录拒绝后的运行态", {"pg_3": {"weight": "10"}})
    lock_path.rmdir()
    lock_path.touch(mode=0o600)
    os.chmod(lock_path, 0o600)
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=11;', "恢复普通锁文件后修改权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证锁目录修复后的修改备份")
    ops.assert_nodes("查看锁目录修复后的运行态", {"pg_3": {"weight": "11"}})
    backup = ops.backup_checkpoint(conf)
    ops.psql('SET NODE WEIGHT pg_3=10;', "恢复锁目录用例节点权重",
            "返回 SET NODE 且命令不报错",
            lambda output: "SET NODE" in output and "ERROR" not in output)
    ops.assert_backup_created(backup, conf,
                             "验证锁目录用例恢复备份")
    ops.diff(before, conf)
    ops.assert_nodes("查看锁目录用例恢复后的运行态", {"pg_3": {"weight": "10"}})



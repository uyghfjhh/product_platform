"""基于 pgcluster 的 fbasecman 环境提供者实现。

替换原有旧脚本，使用 pgcluster 统一编排 14 节点双 MMR 集群的生命周期，
并调用 fbasecman_fixture 初始化角色、表结构与 test_context.yaml。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from framework.environment.provider import EnvironmentProvider
from platform_app.config import load_settings
from platform_app.fbasecman_fixture import prepare
from platform_app.fbasecman_profile import build_profile, profile_paths, save_profile


class FbasecmanPgclusterEnvironmentProvider(EnvironmentProvider):
    """fbasecman 回归测试集群环境适配器 (基于 pgcluster 引擎)。"""

    def __init__(self, regress_env, verbose: bool = True):
        self.regress_env = regress_env
        self.verbose = verbose
        self.settings = load_settings()
        self.target = "mmr.fbasecman_regress"

    def _get_pgcluster_bin(self) -> Path:
        """获取 pgcluster CLI 执行入口。"""
        pgcluster = self.settings.pgcluster_root / "pgcluster"
        if not pgcluster.is_file():
            raise FileNotFoundError(f"未找到 pgcluster 执行入口: {pgcluster}")
        return pgcluster

    def _ensure_profile(self) -> tuple[Path, Path]:
        """确保根据当前环境配置生成了 pgcluster.yaml 和 regress.override.yaml。"""
        env_dict = {
            "id": "default",
            "host": self.regress_env.config["database"].get("mmr_host", "127.0.0.1"),
        }
        db = self.regress_env.config["database"]
        mmr1_port = int(db["ports"]["mmr1"])
        data_root = db.get("mmr_data_root", str(self.settings.data_dir / "cman_pgdata"))
        license_file = db.get("license_file", str(self.settings.license_key_dir.parent / "license.dat"))

        path, override = save_profile(
            self.settings,
            env_dict,
            mmr1_port=mmr1_port,
            data_root=data_root,
            license_file=license_file,
        )
        return path, override

    def _run_pgcluster_cmd(self, action: str, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
        """调用 pgcluster 执行指定的集群动作 (如 create, start, stop, clean)。"""
        pgcluster_path, _ = self._ensure_profile()
        cmd = [
            sys.executable,
            str(self._get_pgcluster_bin()),
            "-f",
            str(pgcluster_path),
            action,
            self.target,
        ]
        if extra_args:
            cmd.extend(extra_args)

        if self.verbose:
            print(f"[pgcluster] 执行动作: {action} ({self.target})", flush=True)

        res = subprocess.run(
            cmd,
            cwd=str(self.settings.pgcluster_root),
            text=True,
            capture_output=not self.verbose,
        )
        if res.returncode != 0:
            err = res.stderr.strip() if res.stderr else f"退出码 {res.returncode}"
            raise RuntimeError(f"pgcluster {action} 失败: {err}")
        return res

    def setup(self, adopt_existing: bool = False):
        """一键部署与初始化集群环境。
        
        步骤 1: 自动生成并校验 pgcluster.yaml
        步骤 2: 调用 pgcluster create 拉起 14 节点集群
        步骤 3: 调用 fbasecman_fixture 准备测试角色、库表与 test_context.yaml
        """
        profile_path, override_path = self._ensure_profile()
        print(f"[setup] 1/3 已生成并校验 pgcluster 部署配置: {profile_path}", flush=True)

        # 启动集群
        self._run_pgcluster_cmd("create")
        print("[setup] 2/3 pgcluster 集群创建与启动成功", flush=True)

        # 准备业务测试夹具与上下文
        prepare(profile_path, override_path)
        print("[setup] 3/3 测试夹具已就绪，测试上下文 test_context.yaml 已生成", flush=True)
        return True

    def clean(self, dry_run: bool = False, adopt_existing: bool = False):
        """清理集群数据目录与残留进程。"""
        if dry_run:
            print("[clean] 预演清理计划: 将清理由 pgcluster 管理的所有实例 PGDATA 与日志", flush=True)
            return True
        return self._run_pgcluster_cmd("clean", ["--yes"])

    def start(self):
        """启动集群内全部节点。"""
        return self._run_pgcluster_cmd("start")

    def restart(self):
        """重启集群内全部节点。"""
        return self._run_pgcluster_cmd("restart")

    def stop(self):
        """停止集群内全部节点。"""
        return self._run_pgcluster_cmd("stop")

    def heal(self):
        """集群自愈: 探测并重新拉起非在线节点。"""
        print("[heal] 正在通过 pgcluster 检查并自愈节点状态...", flush=True)
        return self._run_pgcluster_cmd("start")

    def status_text(self) -> str:
        """获取集群节点与拓扑健康状态描述。"""
        try:
            profile_path, _ = self._ensure_profile()
            cmd = [
                sys.executable,
                str(self._get_pgcluster_bin()),
                "-f",
                str(profile_path),
                "status",
                self.target,
            ]
            res = subprocess.run(
                cmd,
                cwd=str(self.settings.pgcluster_root),
                text=True,
                capture_output=True,
            )
            return res.stdout if res.returncode == 0 else f"获取集群状态失败: {res.stderr}"
        except Exception as exc:
            return f"集群状态查询异常: {exc}"


# 保持与旧接口类名兼容
FbasecmanEnvironmentProvider = FbasecmanPgclusterEnvironmentProvider

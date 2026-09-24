"""HA command suite integration contract."""

from framework.suites.contracts import SuitePlugin
from .manifest import case_items
from .suite import run, show

PLUGIN = SuitePlugin(
    suite_id="ha_commands",
    title="高可用控制台命令及持久化",
    description="SET NODE / SET CLUSTER / REFRESH CLUSTER 等控制台命令及配置文件持久化",
    case_loader=case_items,
    runner=run,
    shower=show,
)

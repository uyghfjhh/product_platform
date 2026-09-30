"""fbasecman environment helpers: node control and health probes.

The former env-provider layer (ClusterManager/EnvState/inventory/setup
scripts) was retired: platform deployment owns environment lifecycle, and
no production caller reached it. Remaining modules are consumed by the
high_availability runtime (cluster_ops) and the stable suite (health).
"""

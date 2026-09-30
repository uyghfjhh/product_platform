"""Fixed cman regression topology, compiled from its product-owned profile."""

import yaml

from .profile import _merge, build_profile


def templates(settings):
    return [
        {
            "id": "cman",
            "title": "fbasecman：两组多活，各六个备库",
            "nodes": 14,
            "base_port": 11011,
            "target": "mmr.fbasecman_regress",
        }
    ]


def compile_template(settings, spec, environment_id):
    environment = {"id": environment_id, "host": spec.host}
    config, regression = build_profile(
        settings,
        environment,
        mmr1_port=spec.base_port,
        data_root=spec.data_root,
        license_file=spec.license_file,
    )
    config["postgresql_installations"]["regress_postgres"]["home"] = spec.home
    config["postgresql_config"]["parameters"].update(spec.parameters)
    overrides = {node.name: node for node in spec.nodes}
    if overrides and set(overrides) != set(config["instances"]):
        raise ValueError("节点覆盖必须包含模板全部节点")
    for name, node in overrides.items():
        config["instances"][name].update(port=node.port, data_dir=node.data_dir)
    regression["database"].update(mmr_postgres_dir=spec.home)
    for group in ("mmr1", "mmr2"):
        regression["database"]["ports"][group] = config["instances"]["test_" + group][
            "port"
        ]
        regression["database"]["ports"][group + "_standbys"] = [
            config["instances"][row["instance"]]["port"]
            for row in config["streaming_clusters"][group]["standbys"]
        ]
    old = yaml.safe_load(
        (settings.product_regress_root("fbasecman") / "regress.yaml").read_text()
    )
    _merge(old, regression)
    return (
        config,
        "mmr.fbasecman_regress",
        {
            "regress.override.yaml": yaml.safe_dump(regression, allow_unicode=True),
            "regress.yaml": yaml.safe_dump(old, allow_unicode=True),
        },
    )

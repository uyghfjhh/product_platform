"""Resolve variable-size database topologies from regression configuration."""


def standby_ports(database, cluster):
    ports = database["ports"]
    list_name = "%s_standbys" % cluster
    if list_name in ports:
        return tuple(ports[list_name])
    prefix = "%s_standby" % cluster
    return tuple(ports["%s%d" % (prefix, index)] for index in range(1, 100)
                 if "%s%d" % (prefix, index) in ports)


def topology_nodes(database):
    result = {
        "mmr1": [("test_mmr1", database["ports"]["mmr1"])],
        "mmr2": [("test_mmr2", database["ports"]["mmr2"])],
    }
    for cluster in ("mmr1", "mmr2"):
        result[cluster].extend(
            ("test_%s_s%d" % (cluster, index), port)
            for index, port in enumerate(standby_ports(database, cluster), 1)
        )
    return result

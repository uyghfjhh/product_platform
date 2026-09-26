from framework.errors import ConfigError


class Topology(object):
    """Product-neutral node roles and semantic node selection."""

    def __init__(self, cluster_name, nodes, groups):
        self.cluster_name = cluster_name
        self.nodes = nodes
        self.groups = groups

    def resolve(self, selector="primary"):
        if selector in self.nodes:
            return selector
        selector = selector or "primary"
        streaming = self.groups.get("streaming") or {}
        logical = self.groups.get("logical") or {}
        members = (self.groups.get("mmr") or {}).get("members") or {}
        if selector in ("primary", "writable"):
            if streaming.get("primary"):
                return streaming["primary"]
            if members:
                return next(iter(members.values()))["primary"]
            if logical.get("publisher"):
                return logical["publisher"]
            return next(iter(self.nodes))
        if selector == "standby" and streaming.get("standbys"):
            return streaming["standbys"][0]
        if selector == "publisher" and logical.get("publisher"):
            return logical["publisher"]
        if selector in ("subscriber", "logical_subscriber"):
            subscribers = logical.get("subscribers") or {}
            if subscribers:
                return next(iter(subscribers))
        if selector.startswith("mmr:"):
            parts = selector.split(":")
            relation = members.get(parts[1]) if len(parts) > 1 else None
            if relation:
                if len(parts) == 2 or parts[2] == "primary":
                    return relation["primary"]
                if parts[2] == "standby" and relation.get("standbys"):
                    return relation["standbys"][0]
        raise ConfigError("cluster %s 无法解析节点选择器: %s" %
                          (self.cluster_name, selector))

    def roles(self):
        roles = {}
        streaming = self.groups.get("streaming") or {}
        if streaming:
            roles[streaming["primary"]] = "primary"
            for node in streaming.get("standbys") or []:
                roles[node] = "standby"
        logical = self.groups.get("logical") or {}
        if logical:
            roles.setdefault(logical["publisher"], "publisher")
            for node in logical.get("subscribers") or {}:
                roles[node] = "logical_subscriber"
        mmr = self.groups.get("mmr") or {}
        for member_name, relation in (mmr.get("members") or {}).items():
            roles[relation["primary"]] = "mmr_primary:%s" % member_name
            for node in relation.get("standbys") or []:
                roles[node] = "mmr_standby:%s" % member_name
        return roles

    def physical_relations(self):
        relations = []
        streaming = self.groups.get("streaming") or {}
        for standby in streaming.get("standbys") or []:
            relations.append((streaming.get("primary"), standby))
        mmr = self.groups.get("mmr") or {}
        for relation in (mmr.get("members") or {}).values():
            for standby in relation.get("standbys") or []:
                relations.append((relation["primary"], standby))
        return relations

    def physical_standbys(self):
        return {standby for unused_primary, standby in self.physical_relations()}

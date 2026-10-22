"""PostgreSQL native logical replication (publication / subscription / slot) lifecycle."""

from .sql import quote_ident, quote_literal


class LogicalRuntimeMixin:
    def _drop_logical(self, name):
        link = self.config.logical_replications[name]
        pub_cluster = link["pub"]["streaming_cluster"]
        sub_cluster = link["sub"]["streaming_cluster"]
        publisher = self._primary(pub_cluster)
        subscriber = self._primary(sub_cluster)
        database = link["sub"].get("database", link["pub"].get("database", "postgres"))
        subscription = "%s_sub" % name
        publication = "%s_pub" % name
        if self._psql(subscriber, "SELECT count(*) FROM pg_subscription WHERE subname=%s" % quote_literal(subscription), database, True) == "1":
            self._psql(subscriber, "ALTER SUBSCRIPTION %s DISABLE" % quote_ident(subscription), database)
            self._psql(subscriber, "DROP SUBSCRIPTION %s" % quote_ident(subscription), database)
        if self._psql(publisher, "SELECT count(*) FROM pg_publication WHERE pubname=%s" % quote_literal(publication), link["pub"].get("database", "postgres"), True) == "1":
            self._psql(publisher, "DROP PUBLICATION %s" % quote_ident(publication), link["pub"].get("database", "postgres"))
        slot = (link["sub"].get("slot") or {}).get("name") or "%s_slot" % name
        self._psql(publisher, "SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots "
                   "WHERE slot_name=%s AND NOT active" % quote_literal(slot), link["pub"].get("database", "postgres"), True)

    def create_logical(self, name):
        link = self.config.logical_replications[name]
        pub_cluster = link["pub"]["streaming_cluster"]
        sub_cluster = link["sub"]["streaming_cluster"]
        self._progress("创建逻辑复制 logical.%s" % name)
        self.create_streaming(pub_cluster, {"wal_level": "logical", "max_replication_slots": 16,
                                            "max_wal_senders": 16, "max_logical_replication_workers": 8})
        self.create_streaming(sub_cluster, {"wal_level": "logical", "max_replication_slots": 16,
                                            "max_wal_senders": 16, "max_logical_replication_workers": 8})
        publisher = self._primary(pub_cluster)
        subscriber = self._primary(sub_cluster)
        database = link["pub"].get("database", "postgres")
        publication, subscription = "%s_pub" % name, "%s_sub" % name
        slot = (link["sub"].get("slot") or {}).get("name") or "%s_slot" % name
        self._progress("创建 publication %s" % publication)
        tables = link["pub"].get("tables") or "all"
        options = []
        if link["pub"].get("via_partition_root"):
            options.append("publish_via_partition_root = true")
        if link["pub"].get("publish"):
            options.append("publish = %s" % quote_literal(link["pub"]["publish"]))
        suffix = " WITH (%s)" % ", ".join(options) if options else ""
        if tables == "all":
            ddl = "CREATE PUBLICATION %s FOR ALL TABLES%s" % (quote_ident(publication), suffix)
        else:
            ddl = "CREATE PUBLICATION %s FOR TABLE %s%s" % (
                quote_ident(publication),
                ", ".join(
                    ".".join(quote_ident(part) for part in table.split("."))
                    for table in tables
                ),
                suffix,
            )
        if self._psql(publisher, "SELECT count(*) FROM pg_publication WHERE pubname=%s" % quote_literal(publication), database, True) != "1":
            self._psql(publisher, ddl, database)
        p = self.config.instance(publisher)
        conn = "host=%s port=%s user=postgres dbname=%s application_name=%s" % (p["host_config"]["address"], p["port"], database, subscription)
        exists = self._psql(subscriber, "SELECT count(*) FROM pg_subscription WHERE subname=%s" % quote_literal(subscription), database, True)
        self._progress("创建或启用 subscription %s" % subscription)
        if exists != "1":
            failover = "true" if (link["sub"].get("slot") or {}).get("failover", False) else "false"
            self._psql(subscriber, "CREATE SUBSCRIPTION %s CONNECTION %s PUBLICATION %s WITH (slot_name=%s, failover=%s)" %
                       (quote_ident(subscription), quote_literal(conn), quote_ident(publication), quote_literal(slot), failover), database)
        else:
            self._psql(subscriber, "ALTER SUBSCRIPTION %s CONNECTION %s; ALTER SUBSCRIPTION %s ENABLE" %
                       (quote_ident(subscription), quote_literal(conn), quote_ident(subscription)), database)
        # An existing subscription can lose its publisher-side slot (e.g. a
        # test fixture dropped it); recreate the slot so the apply worker can
        # stream again instead of failing forever.
        if self._psql(publisher, "SELECT count(*) FROM pg_replication_slots WHERE slot_name=%s" % quote_literal(slot), database, True) != "1":
            self._progress("补齐发布端逻辑复制槽 %s" % slot)
            self._psql(publisher, "SELECT pg_create_logical_replication_slot(%s, 'pgoutput')" % quote_literal(slot), database)
        return "创建完成: logical.%s" % name

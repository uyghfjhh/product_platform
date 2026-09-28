import pytest

from platform_regress.evidence.config_diff import (
    strip_inline_comment,
    parse_semantic_objects,
    command_mutation_scope,
    semantic_config_diff,
)


def test_strip_inline_comment():
    assert strip_inline_comment("host 127.0.0.1") == "host 127.0.0.1"
    assert strip_inline_comment("host 127.0.0.1 # local IP") == "host 127.0.0.1"
    assert strip_inline_comment('name "foo#bar" # inline comment') == 'name "foo#bar"'
    assert strip_inline_comment('quote "\\"#not_comment\\"" # real comment') == 'quote "\\"#not_comment\\""'


SAMPLE_CONFIG = """
group "default" {
    write_cluster "cluster1"
    promoted_cluster "cluster1"
}

datasources "ds1" {
    cluster_name "cluster1"
    host "127.0.0.1"
    port 5432
    status "active"
    weight 100
}

datasources "ds2" {
    cluster_name "cluster2"
    host "127.0.0.1"
    port 5433
    status "active"
    weight 50
}
"""


def test_parse_semantic_objects():
    objects, order = parse_semantic_objects(SAMPLE_CONFIG)
    assert order == [("group", "default"), ("datasources", "ds1"), ("datasources", "ds2")]
    assert objects[("group", "default")]["write_cluster"] == '"cluster1"'
    assert objects[("datasources", "ds1")]["port"] == "5432"

    # Duplicate object
    dup_config = SAMPLE_CONFIG + '\ndatasources "ds1" { port 5434 }'
    with pytest.raises(ValueError, match="duplicate object"):
        parse_semantic_objects(dup_config)

    # Duplicate field
    dup_field_config = 'datasources "ds1" { port 5432\n port 5433 }'
    with pytest.raises(ValueError, match="duplicate field"):
        parse_semantic_objects(dup_field_config)


def test_command_mutation_scope_and_semantic_diff():
    # Test valid WRITE command
    sql_write = 'SET CLUSTER WRITE cluster2 IN GROUP default;'
    after_config_write = SAMPLE_CONFIG.replace(
        'write_cluster "cluster1"\n    promoted_cluster "cluster1"',
        'write_cluster "cluster2"\n    promoted_cluster "cluster1"',
    )
    valid, diff = semantic_config_diff(SAMPLE_CONFIG, after_config_write, sql_write)
    assert valid is True
    assert "group default.write_cluster: \"cluster1\" -> \"cluster2\"" in diff

    # Test unexpected field mutation
    unexpected_after = after_config_write.replace('weight 100', 'weight 200')
    valid_bad, diff_bad = semantic_config_diff(SAMPLE_CONFIG, unexpected_after, sql_write)
    assert valid_bad is False
    assert "[非预期]" in diff_bad

    # Test SET CLUSTER PARTED
    sql_parted = 'SET CLUSTER PARTED cluster1;'
    after_config_parted = SAMPLE_CONFIG.replace(
        'cluster_name "cluster1"\n    host "127.0.0.1"\n    port 5432\n    status "active"',
        'cluster_name "cluster1"\n    host "127.0.0.1"\n    port 5432\n    status "parted"',
    )
    valid_parted, diff_parted = semantic_config_diff(SAMPLE_CONFIG, after_config_parted, sql_parted)
    assert valid_parted is True

    # Test structure error in config (duplicate object)
    dup_config = SAMPLE_CONFIG + '\ndatasources "ds1" { port 5434 }'
    valid_err, msg_err = semantic_config_diff(dup_config, SAMPLE_CONFIG, sql_write)
    assert valid_err is False
    assert "配置结构错误" in msg_err

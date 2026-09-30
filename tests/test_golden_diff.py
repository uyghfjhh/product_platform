"""golden_diff 规范化与对比行为。"""

import platform_regress.golden_diff as gd


def test_normalize_dynamic_fields():
    raw = (
        'connection ID 42 closed\n'
        '2026-09-30 12:00:01.123 +08:00 info [icae3a5b40e183 msee446b3cd800] (fb_monitor) new server connection\n'
        'working time: 2982us / connect time: 88 usec\n'
        '等待 5.5 秒\n'
        'id=77621 pid 9021\n'
        'failed to connect error:111\n'
        '端口 11021, 15432)\n'
        'exec abcdef0123456789abcdef0123456789 done\n'
    )
    out = gd.normalize(raw)
    for needle in ('42', '2026', 'icae3a5b40e183', '2982', '88 ', '5.5', '77621',
                   '9021', '111', '11021', '15432', 'abcdef0123456789'):
        assert needle not in out, needle
    assert '<CID>' in out and '<TS>' in out and '[<TOK>]' in out
    assert '等待 <N> 秒' in out and 'id=<N>' in out and 'pid=<N>' in out


def test_normalize_counter_cells_only_on_ts_rows():
    plain = '| node | role | 3 |'                       # 无时间戳 → 计数保留
    evidence = '| node | UP_STABLE | 2026-09-30 01:02:03 | NONE | 19 |'
    assert gd.normalize(plain) == '| node | role | 3 |'
    out = gd.normalize(evidence)
    assert '<TS>' in out and '| <N>' in out and '19' not in out


def test_diff_text_identical_after_normalization():
    golden = '步骤1 PASS 2026-09-29 01:00:00\n耗时 10us'
    actual = '步骤1 PASS 2026-09-30 02:00:00\n耗时 99us'
    assert gd.diff_text(golden, actual, 'g', 'a') == ''


def test_diff_text_semantic_change_visible():
    golden = '步骤1 PASS\n预期: VALID\n实际: VALID'
    actual = '步骤1 FAIL\n预期: VALID\n实际: DEGRADED'
    diff = gd.diff_text(golden, actual, 'g', 'a')
    assert '-步骤1 PASS' in diff and '+步骤1 FAIL' in diff


def test_compare_dirs_pairs_and_missing(tmp_path, capsys):
    g = tmp_path / 'golden'
    a = tmp_path / 'actual'
    g.mkdir()
    a.mkdir()
    (g / 'one.report.txt').write_text('ok 2026-01-01 00:00:00', encoding='utf-8')
    (a / 'one.report.txt').write_text('ok 2026-01-02 00:00:00', encoding='utf-8')
    (g / 'gone.report.txt').write_text('x', encoding='utf-8')
    (a / 'new.report.txt').write_text('y', encoding='utf-8')
    assert gd.compare_paths(g, a) == 2
    out = capsys.readouterr().out
    assert 'one: IDENTICAL' in out
    assert 'gone' in out and 'new' in out


def test_capture_names_by_case_dir(tmp_path):
    src = tmp_path / 'src' / 'suite.target' / 'artifacts' / 'exec1'
    src.mkdir(parents=True)
    (src / 'report.txt').write_text('r', encoding='utf-8')
    dest = tmp_path / 'golden'
    assert gd.capture(tmp_path / 'src', dest) == 1
    assert (dest / 'suite.target.report.txt').read_text() == 'r'


def test_main_usage_error(capsys):
    assert gd.main([]) == 2

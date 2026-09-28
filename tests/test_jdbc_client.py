"""Platform-native tests for the shared JDBC client helpers."""

import os

import pytest

from platform_regress.clients import jdbc
from platform_regress.clients.jdbc import JdbcError


class TestResolveJar:
    def test_finds_versioned_pgjdbc_jar(self, tmp_path):
        jar = tmp_path / "postgresql-42.7.7.jar"
        jar.write_bytes(b"jar")
        assert jdbc.resolve_jar(tmp_path, "42.7.7") == jar

    def test_missing_jar_raises(self, tmp_path):
        with pytest.raises(JdbcError, match="missing jdbc jar"):
            jdbc.resolve_jar(tmp_path, "42.7.7")

    def test_custom_pattern(self, tmp_path):
        jar = tmp_path / "driver-1.0.jar"
        jar.write_bytes(b"jar")
        assert jdbc.resolve_jar(tmp_path, "1.0", pattern="driver-%s.jar") == jar


class TestBuildUrl:
    def test_matches_legacy_format(self):
        assert jdbc.build_url(
            "localhost", 15011, "postgres",
            {"prepareThreshold": 1, "preferQueryMode": "extended"},
        ) == ("jdbc:postgresql://localhost:15011/postgres?"
              "prepareThreshold=1&preferQueryMode=extended")

    def test_skips_none_values(self):
        assert jdbc.build_url(
            "127.0.0.1", 5432, "console",
            {"a": "1", "skip": None, "b": "2"},
        ) == "jdbc:postgresql://127.0.0.1:5432/console?a=1&b=2"

    def test_empty_options_keep_trailing_question_mark(self):
        assert jdbc.build_url("h", 1, "d") == "jdbc:postgresql://h:1/d?"
        assert jdbc.build_url("h", 1, "d", {}) == "jdbc:postgresql://h:1/d?"


class TestArgvBuilders:
    def test_classpath_joins_with_os_pathsep(self):
        assert jdbc.classpath("/dir", "/lib/j.jar") == "/dir%s/lib/j.jar" % os.pathsep

    def test_javac_argv_without_dest_dir(self):
        assert jdbc.javac_argv("/l/postgresql-42.7.7.jar", "/s/GC_x.java") == [
            "javac", "-cp", "/l/postgresql-42.7.7.jar", "/s/GC_x.java",
        ]

    def test_javac_argv_with_dest_dir_and_extra_classpath(self):
        assert jdbc.javac_argv(
            "/j.jar", "/s/x.java", dest_dir="/out", extra_classpath=("/e.jar",),
        ) == ["javac", "-cp", "/j.jar%s/e.jar" % os.pathsep, "-d", "/out", "/s/x.java"]

    def test_java_argv(self):
        assert jdbc.java_argv("/d:/j.jar", "GC_x", "url", "postgres", "") == [
            "java", "-cp", "/d:/j.jar", "GC_x", "url", "postgres", "",
        ]

    def test_java_argv_preserves_empty_and_extra_arguments(self):
        argv = jdbc.java_argv("cp", "Cls", "u", "admin", "", "a", "b")
        assert argv[6] == "" and argv[-2:] == ["a", "b"]


class TestSourceAssets:
    def test_source_file_returns_existing_path(self, tmp_path):
        src = tmp_path / "GC_demo.java"
        src.write_text("class GC_demo {}", encoding="utf-8")
        assert jdbc.source_file(tmp_path, "GC_demo.java") == src

    def test_source_file_missing_raises(self, tmp_path):
        with pytest.raises(JdbcError, match="missing jdbc driver source"):
            jdbc.source_file(tmp_path, "GC_missing.java")

    def test_stage_source_copies_into_target_dir(self, tmp_path):
        assets = tmp_path / "assets"
        assets.mkdir()
        src = assets / "GC_demo.java"
        src.write_text("class GC_demo {}", encoding="utf-8")
        target = jdbc.stage_source(src, tmp_path / "drivers")
        assert target == tmp_path / "drivers" / "GC_demo.java"
        assert target.read_text(encoding="utf-8") == "class GC_demo {}"
        assert src.exists()

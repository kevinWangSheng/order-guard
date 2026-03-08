"""Tests for DBHub integration (T20)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from order_guard.mcp.models import (
    DBHubDatabaseConfig,
    DBHubSecurityConfig,
    MCPServerConfig,
)
from order_guard.mcp.dbhub import (
    build_dbhub_command,
    build_dbhub_toml,
    prepare_dbhub_config,
    write_dbhub_toml,
)
from order_guard.mcp.manager import MCPManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_dbhub_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="test-warehouse",
        type="dbhub",
        databases=[
            DBHubDatabaseConfig(alias="warehouse", dsn="sqlite:///data/test_warehouse.db"),
        ],
        security=DBHubSecurityConfig(readonly=True, max_rows=1000),
    )


@pytest.fixture
def multi_db_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="multi-db",
        type="dbhub",
        databases=[
            DBHubDatabaseConfig(
                alias="orders",
                dsn="mysql://ro:pass@host:3306/orders_db",
                query_timeout=10,
            ),
            DBHubDatabaseConfig(
                alias="inventory",
                dsn="sqlite:///data/inventory.db",
            ),
        ],
        security=DBHubSecurityConfig(readonly=True, max_rows=500),
    )


# ---------------------------------------------------------------------------
# TOML generation
# ---------------------------------------------------------------------------

class TestBuildDBHubToml:
    def test_single_sqlite(self, sqlite_dbhub_config: MCPServerConfig) -> None:
        toml = build_dbhub_toml(sqlite_dbhub_config)
        assert '[[sources]]' in toml
        assert 'id = "warehouse"' in toml
        assert 'dsn = "sqlite:///data/test_warehouse.db"' in toml
        assert '[[tools]]' in toml
        assert 'name = "execute_sql"' in toml
        assert 'source = "warehouse"' in toml
        assert 'readonly = true' in toml
        assert 'max_rows = 1000' in toml

    def test_no_query_timeout_omitted(self, sqlite_dbhub_config: MCPServerConfig) -> None:
        toml = build_dbhub_toml(sqlite_dbhub_config)
        assert 'query_timeout' not in toml

    def test_query_timeout_included(self, multi_db_config: MCPServerConfig) -> None:
        toml = build_dbhub_toml(multi_db_config)
        assert 'query_timeout = 10' in toml

    def test_multi_database(self, multi_db_config: MCPServerConfig) -> None:
        toml = build_dbhub_toml(multi_db_config)
        assert toml.count('[[sources]]') == 2
        assert 'id = "orders"' in toml
        assert 'id = "inventory"' in toml
        assert toml.count('[[tools]]') == 2
        assert 'source = "orders"' in toml
        assert 'source = "inventory"' in toml

    def test_readonly_false(self) -> None:
        config = MCPServerConfig(
            name="rw-db",
            type="dbhub",
            databases=[DBHubDatabaseConfig(alias="main", dsn="sqlite:///test.db")],
            security=DBHubSecurityConfig(readonly=False, max_rows=100),
        )
        toml = build_dbhub_toml(config)
        assert 'readonly = false' in toml
        assert 'max_rows = 100' in toml


# ---------------------------------------------------------------------------
# TOML file writing
# ---------------------------------------------------------------------------

class TestWriteDBHubToml:
    def test_write_to_base_dir(self, sqlite_dbhub_config: MCPServerConfig, tmp_path: Path) -> None:
        toml_path = write_dbhub_toml(sqlite_dbhub_config, base_dir=tmp_path)
        assert toml_path.exists()
        assert toml_path.name == "dbhub-test-warehouse.toml"
        content = toml_path.read_text()
        assert '[[sources]]' in content

    def test_write_to_temp(self, sqlite_dbhub_config: MCPServerConfig) -> None:
        toml_path = write_dbhub_toml(sqlite_dbhub_config)
        assert toml_path.exists()
        content = toml_path.read_text()
        assert '[[sources]]' in content
        # Cleanup
        toml_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Command building
# ---------------------------------------------------------------------------

class TestBuildDBHubCommand:
    def test_command_structure(self, sqlite_dbhub_config: MCPServerConfig, tmp_path: Path) -> None:
        toml_path = tmp_path / "dbhub.toml"
        toml_path.write_text("test")
        command, args = build_dbhub_command(sqlite_dbhub_config, toml_path)
        assert command == "npx"
        assert "-y" in args
        assert "@bytebase/dbhub" in args
        assert f"--config={toml_path}" in args
        assert "stdio" in args


# ---------------------------------------------------------------------------
# prepare_dbhub_config
# ---------------------------------------------------------------------------

class TestPrepareDBHubConfig:
    def test_resolves_to_stdio(self, sqlite_dbhub_config: MCPServerConfig) -> None:
        resolved = prepare_dbhub_config(sqlite_dbhub_config)
        assert resolved.transport == "stdio"
        assert resolved.command == "npx"
        assert "@bytebase/dbhub" in resolved.args
        assert any("--config=" in a for a in resolved.args)

    def test_no_databases_raises(self) -> None:
        config = MCPServerConfig(name="empty", type="dbhub")
        with pytest.raises(ValueError, match="requires at least one database"):
            prepare_dbhub_config(config)

    def test_preserves_name_and_env(self) -> None:
        config = MCPServerConfig(
            name="my-db",
            type="dbhub",
            databases=[DBHubDatabaseConfig(alias="x", dsn="sqlite:///t.db")],
            env={"MY_VAR": "value"},
        )
        resolved = prepare_dbhub_config(config)
        assert resolved.name == "my-db"
        assert resolved.env == {"MY_VAR": "value"}
        assert resolved.type == "dbhub"


# ---------------------------------------------------------------------------
# MCPManager DBHub integration
# ---------------------------------------------------------------------------

class TestMCPManagerDBHub:
    def test_resolve_dbhub_config(self, sqlite_dbhub_config: MCPServerConfig) -> None:
        """MCPManager should auto-resolve DBHub configs."""
        resolved = MCPManager._resolve_config(sqlite_dbhub_config)
        assert resolved.command == "npx"
        assert resolved.transport == "stdio"

    def test_generic_config_passthrough(self) -> None:
        """Generic configs should pass through unchanged."""
        config = MCPServerConfig(
            name="generic-mcp",
            type="generic",
            transport="stdio",
            command="some-cmd",
            args=["--flag"],
        )
        resolved = MCPManager._resolve_config(config)
        assert resolved.command == "some-cmd"
        assert resolved.args == ["--flag"]

    def test_disabled_dbhub_not_connected(self) -> None:
        """Disabled DBHub config should not be added to connections."""
        config = MCPServerConfig(
            name="disabled-db",
            type="dbhub",
            enabled=False,
            databases=[DBHubDatabaseConfig(alias="x", dsn="sqlite:///t.db")],
        )
        manager = MCPManager([config])
        assert "disabled-db" not in manager.list_connections()


# ---------------------------------------------------------------------------
# Config model tests
# ---------------------------------------------------------------------------

class TestDBHubModels:
    def test_dbhub_database_config(self) -> None:
        db = DBHubDatabaseConfig(alias="warehouse", dsn="sqlite:///data/w.db")
        assert db.alias == "warehouse"
        assert db.query_timeout is None

    def test_dbhub_database_config_with_timeout(self) -> None:
        db = DBHubDatabaseConfig(alias="erp", dsn="mysql://h/db", query_timeout=30)
        assert db.query_timeout == 30

    def test_dbhub_security_defaults(self) -> None:
        sec = DBHubSecurityConfig()
        assert sec.readonly is True
        assert sec.max_rows == 1000

    def test_mcp_server_config_type_default(self) -> None:
        config = MCPServerConfig(name="test")
        assert config.type == "generic"
        assert config.databases == []

    def test_mcp_server_config_dbhub(self) -> None:
        config = MCPServerConfig(
            name="test",
            type="dbhub",
            databases=[DBHubDatabaseConfig(alias="a", dsn="sqlite:///a.db")],
            security=DBHubSecurityConfig(readonly=True, max_rows=500),
        )
        assert config.type == "dbhub"
        assert len(config.databases) == 1
        assert config.security.max_rows == 500


# ---------------------------------------------------------------------------
# Settings integration
# ---------------------------------------------------------------------------

class TestSettingsIntegration:
    def test_settings_mcp_config_with_dbhub_fields(self) -> None:
        """Verify settings MCPServerConfig accepts DBHub fields."""
        from order_guard.config.settings import MCPServerConfig as SettingsMCPConfig

        config = SettingsMCPConfig(
            name="test-db",
            type="dbhub",
            databases=[{"alias": "db", "dsn": "sqlite:///t.db"}],
            security={"readonly": True, "max_rows": 500},
        )
        assert config.type == "dbhub"
        assert len(config.databases) == 1
        assert config.security.readonly is True

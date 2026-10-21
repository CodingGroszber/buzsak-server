from __future__ import annotations

import sqlite3

from deploy.backup_db import _schema_needs_backup


def _database_with_version(path, version):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    if version is not None:
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, 'test')",
            (version,),
        )
    connection.commit()
    connection.close()


def test_current_schema_does_not_create_a_redundant_backup(tmp_path):
    path = tmp_path / "current.sqlite3"
    _database_with_version(path, 3)

    assert _schema_needs_backup(path, latest_version=3) is False


def test_pending_migration_requires_a_backup(tmp_path):
    path = tmp_path / "old.sqlite3"
    _database_with_version(path, 2)

    assert _schema_needs_backup(path, latest_version=3) is True


def test_unversioned_existing_database_requires_a_backup(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE legacy_data (value TEXT)")
    connection.close()

    assert _schema_needs_backup(path, latest_version=3) is True


def test_missing_database_does_not_require_a_backup(tmp_path):
    assert _schema_needs_backup(
        tmp_path / "missing.sqlite3", latest_version=3) is False

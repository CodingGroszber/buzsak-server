from __future__ import annotations

from datetime import datetime, timedelta, timezone

from buzsak_pi3_server import db, schema
from buzsak_pi3_server.config import DatabaseConfig
from buzsak_pi3_server.security.credentials import (
    authenticate_token,
    issue_credential,
    revoke_credential,
    token_digest,
)


def test_issued_credential_persists_only_digest_and_authenticates(tmp_path):
    connection = db.connect(DatabaseConfig(
        path=str(tmp_path / "auth.sqlite3")))
    try:
        schema.apply_migrations(connection)
        now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

        credential_id, token = issue_credential(
            connection, "android-owner", "operator", now=now)

        stored_hash = connection.execute(
            "SELECT token_hash FROM api_credentials WHERE credential_id = ?",
            (credential_id,),
        ).fetchone()[0]
        principal = authenticate_token(connection, token, now=now)

        assert stored_hash == token_digest(token)
        assert token not in stored_hash
        assert principal is not None
        assert principal.principal_id == "android-owner"
        assert principal.role == "operator"
    finally:
        connection.close()


def test_expired_and_revoked_credentials_do_not_authenticate(tmp_path):
    connection = db.connect(DatabaseConfig(
        path=str(tmp_path / "auth.sqlite3")))
    try:
        schema.apply_migrations(connection)
        now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
        credential_id, token = issue_credential(
            connection,
            "android-owner",
            "operator",
            expires_at=now + timedelta(minutes=1),
            now=now,
        )

        assert authenticate_token(connection, token, now=now) is not None
        assert authenticate_token(
            connection, token, now=now + timedelta(minutes=1)) is None
        assert revoke_credential(connection, credential_id, now=now)
        assert authenticate_token(connection, token, now=now) is None
        assert not revoke_credential(connection, credential_id, now=now)
    finally:
        connection.close()


def test_issue_rejects_invalid_principal_role_and_expiry(tmp_path):
    connection = db.connect(DatabaseConfig(
        path=str(tmp_path / "auth.sqlite3")))
    try:
        schema.apply_migrations(connection)
        now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)

        for principal, role, expiry in (
            ("", "viewer", None),
            ("owner", "root", None),
            ("owner", "viewer", now),
        ):
            try:
                issue_credential(
                    connection, principal, role, expires_at=expiry, now=now)
            except ValueError:
                pass
            else:
                raise AssertionError("invalid credential request was accepted")
    finally:
        connection.close()

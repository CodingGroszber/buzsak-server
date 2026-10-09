"""Hashed, revocable API bearer credentials (SEC-05, SEC-06, SEC-07, SEC-10)."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

CredentialRole = Literal["viewer", "operator", "admin"]
_VALID_ROLES = frozenset({"viewer", "operator", "admin"})


@dataclass(frozen=True)
class Principal:
    credential_id: str
    principal_id: str
    role: CredentialRole


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def token_digest(token: str) -> str:
    if not token:
        raise ValueError("token must not be empty")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_credential(
    connection: sqlite3.Connection,
    principal_id: str,
    role: CredentialRole,
    *,
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Persist a token digest and return its credential id and one-time token."""
    if not principal_id.strip():
        raise ValueError("principal_id must not be empty")
    if role not in _VALID_ROLES:
        raise ValueError("role must be viewer, operator, or admin")
    now = now or datetime.now(timezone.utc)
    if expires_at is not None and expires_at <= now:
        raise ValueError("expires_at must be in the future")

    credential_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    connection.execute(
        "INSERT INTO api_credentials "
        "(credential_id, principal_id, role, token_hash, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            credential_id,
            principal_id,
            role,
            token_digest(token),
            _iso(now),
            _iso(expires_at) if expires_at else None,
        ),
    )
    return credential_id, token


def authenticate_token(
    connection: sqlite3.Connection,
    token: str,
    *,
    now: datetime | None = None,
) -> Principal | None:
    if not token:
        return None
    now_text = _iso(now or datetime.now(timezone.utc))
    row = connection.execute(
        "SELECT credential_id, principal_id, role FROM api_credentials "
        "WHERE token_hash = ? AND revoked_at IS NULL "
        "AND (expires_at IS NULL OR expires_at > ?)",
        (token_digest(token), now_text),
    ).fetchone()
    if row is None:
        return None
    return Principal(credential_id=row[0], principal_id=row[1], role=row[2])


def authenticate_credential_id(
    connection: sqlite3.Connection,
    credential_id: str,
    *,
    now: datetime | None = None,
) -> Principal | None:
    now_text = _iso(now or datetime.now(timezone.utc))
    row = connection.execute(
        "SELECT credential_id, principal_id, role FROM api_credentials "
        "WHERE credential_id = ? AND revoked_at IS NULL "
        "AND (expires_at IS NULL OR expires_at > ?)",
        (credential_id, now_text),
    ).fetchone()
    if row is None:
        return None
    return Principal(credential_id=row[0], principal_id=row[1], role=row[2])


def revoke_credential(
    connection: sqlite3.Connection,
    credential_id: str,
    *,
    now: datetime | None = None,
) -> bool:
    cursor = connection.execute(
        "UPDATE api_credentials SET revoked_at = ? "
        "WHERE credential_id = ? AND revoked_at IS NULL",
        (_iso(now or datetime.now(timezone.utc)), credential_id),
    )
    return cursor.rowcount == 1

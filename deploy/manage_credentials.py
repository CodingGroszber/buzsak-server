"""Issue or revoke API bearer credentials on the configured server database.

Run on the target with BUZSAK_CONFIG set. Issued tokens are printed once;
store them in the client's protected credential store and do not paste them
into logs, chat, source control, or shell history.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from buzsak_pi3_server import db, schema
from buzsak_pi3_server.config import load_config_from_env
from buzsak_pi3_server.security.credentials import issue_credential, revoke_credential


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    issue = subparsers.add_parser("issue")
    issue.add_argument("principal_id")
    issue.add_argument("role", choices=("viewer", "operator", "admin"))
    revoke = subparsers.add_parser("revoke")
    revoke.add_argument("credential_id")
    args = parser.parse_args()

    config = load_config_from_env()
    connection = db.connect(config.database)
    try:
        schema.require_supported_version(connection)
        if schema.current_version(connection) < 3:
            parser.error(
                "database migration 0003 must be applied before credential management")
        if args.operation == "issue":
            credential_id, token = issue_credential(
                connection,
                args.principal_id,
                args.role,
                now=datetime.now(timezone.utc),
            )
            print(f"credential_id={credential_id}")
            print(f"token={token}")
        else:
            revoked = revoke_credential(
                connection, args.credential_id, now=datetime.now(timezone.utc))
            if not revoked:
                parser.error("credential does not exist or is already revoked")
            print(f"revoked credential_id={args.credential_id}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()

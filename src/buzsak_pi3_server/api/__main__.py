"""Development-only runner for the web/API process.

Production deployments must use a production WSGI server against
`buzsak_pi3_server.api.wsgi:app` instead of this entry point (API-01).
"""

from __future__ import annotations

import logging

from buzsak_pi3_server.api import create_app
from buzsak_pi3_server.config import load_config_from_env
from buzsak_pi3_server.logging_setup import configure_logging

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging(service_name="web")
    config = load_config_from_env()
    logger.warning(
        "Running Flask's development server; production deployments must use "
        "a production WSGI server (gunicorn buzsak_pi3_server.api.wsgi:app) per API-01."
    )
    app = create_app(config)
    app.run(host=config.server.bind_host, port=config.server.bind_port)


if __name__ == "__main__":
    main()

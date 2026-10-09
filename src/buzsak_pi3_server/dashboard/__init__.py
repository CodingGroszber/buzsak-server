"""Dashboard web application (UI-01 through UI-08).

Isolated on purpose: this package only talks to the rest of the project
through :mod:`buzsak_pi3_server.dashboard.queries` (read-only SQLite
reads) and :mod:`buzsak_pi3_server.config` (server-side settings already
loaded by the web/API process). It does not import the poller,
dispatcher, or adapter modules, and it never opens a connection to a
device — only to the local database (ARC-03, UI-05).

The JSON produced by :func:`buzsak_pi3_server.dashboard.queries.build_dashboard_state`
is the same contract the bundled HTML/JS renders from `fetch()`. That
contract is deliberately plain data (no HTML fragments, no server-side
rendering of dynamic content) so a future native client can consume it
without depending on this package's templates or static assets.
"""

from __future__ import annotations

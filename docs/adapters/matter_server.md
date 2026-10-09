# Matter-server / Sonoff MiniD adapter contract (DEV-09, DEV-10)

Approved scope change (2026-09-30): read-only telemetry for the two
already-commissioned Sonoff MiniD switches is in scope (DEV-09), plus one
narrow, verified momentary "pulse" control action for these two
garage-relay devices only (DEV-10). Any other Matter command execution
remains deferred until the general command dispatcher exists (Section 9).

Base address: `ws://192.168.1.95:5580/ws` — an existing
`ghcr.io/home-assistant-libs/python-matter-server:stable` instance already
running on `rpi3` (Docker container `matter-server`, port `5580`). This
server, not this repo, owns Matter commissioning/fabric state.

Verified by connecting live (read-only) to the running instance on
2026-09-30 and inspecting its actual `get_nodes` response — there is no
firmware source to read here (unlike the PLC/valve controller); the
contract below is the real observed WebSocket protocol and payload shape,
not vendor documentation.

## Protocol

Plain WebSocket, JSON messages, no authentication observed (LAN-only;
SEC-03 applies same as the other two devices).

1. On connect, the server immediately sends one unsolicited "server info"
   message (schema/SDK version, fabric id — no per-node data). A client
   must read and discard this before sending anything.
2. Client sends `{"message_id": "<id>", "command": "get_nodes"}`.
3. Server replies `{"message_id": "<id>", "result": [ <node>, ... ]}` with
   **all** commissioned nodes in one response (both Sonoff switches came
   back together). A single-node `get_node` command with a `node_id` arg
   was tried live and returned `{"message_id": ..., "error_code": 8, "details": ""}`
   — not a supported shape for this server version; do not use it.
4. On error, the server replies with `error_code`/`details` instead of
   `result`; treat any response containing `error_code` as a transport
   failure (`PollTransportError`), not a parse failure.

## Node shape (per element of `get_nodes`'s `result` list)

Each entry represents one Matter node (one physical device) with a flat
`attributes` dict keyed `"<endpoint>/<cluster_id>/<attribute_id>"` (decimal
Matter cluster/attribute IDs, not names).

```json
{
  "node_id": 1,
  "available": true,
  "is_bridge": false,
  "attributes": {
    "0/40/1": "SONOFF",
    "0/40/3": "SONOFF MINI-D Wi-Fi Smart Switch",
    "0/40/15": "25517000034110",
    "1/6/0": false
  }
}
```

Fields this adapter reads (DEV-01):
- `node_id` (int) — stable identifier for this Matter node; matches the
  `node_id` encoded in this server's device config address (see below).
- `available` (bool) — the Matter-server's own reachability signal for
  this node; used directly as this device's connectivity/health input,
  analogous to the PLC/valve controller's poll-success tracking.
- `attributes["0/40/1"]` — Basic Information cluster (`0x0028`=40),
  attribute `VendorName` (`0x0001`=1). Always a string when present.
- `attributes["0/40/3"]` — Basic Information cluster, attribute
  `ProductName` (`0x0003`=3). Always a string when present.
- `attributes["0/40/15"]` — Basic Information cluster, attribute
  `SerialNumber` (`0x000F`=15). Always a string when present.
- `attributes["1/6/0"]` — endpoint 1 (the switch's functional endpoint,
  not endpoint 0's node-wide administrative clusters), OnOff cluster
  (`0x0006`=6), attribute `OnOff` (`0x0000`=0). `true`/`false`/`null`
  observed; `null` was seen before the server had ever read the attribute
  from the device — treat `null` as invalid/unavailable, not `false`
  (DEV-05: a missing read must never become a synthetic `false`).

Both commissioned nodes were confirmed to be
`"SONOFF MINI-D Wi-Fi Smart Switch"` at inspection time. Real node IDs
observed were **1 and 3** (not 1 and 2 — commissioning order left a gap;
do not assume sequential IDs).

## Device address encoding

This server's `DeviceConfig.address` can only carry one string per device,
but two physical Sonoff switches share one Matter-server connection. The
node ID is encoded as a URL fragment on the shared WS endpoint:

```yaml
- id: "sonoff-1"
  kind: "sonoff-minid"
  address: "ws://192.168.1.95:5580/ws#node_id=1"
- id: "sonoff-2"
  kind: "sonoff-minid"
  address: "ws://192.168.1.95:5580/ws#node_id=3"
```

The poller calls `get_nodes` once per device poll (there is no verified
single-node query — see above) and picks out the matching `node_id` from
the result list; two devices therefore mean two independent `get_nodes`
calls per second at this server's default polling interval. That is
negligible load for a local server on the same Pi and was not optimized
further (YAGNI, matches the project's existing "small fixed fleet" stance
in `poller/scheduler.py`).

## Not part of this contract

- Everything else in `attributes` (network commissioning, operational
  credentials/certificates, general diagnostics, access control, etc.) —
  present in the real payload but not read or persisted by this adapter.
  In particular, operational certificate/key material appears under
  cluster `62` (`OperationalCredentials`) — never log or persist that
  cluster's attributes (DEV-06, SEC-07).
- Any Matter command beyond the single verified `pulse` action below
  (e.g. a persistent OnOff toggle) — deferred (DEV-09, DEV-10).
- Commissioning new nodes, fabric management, Thread/Wi-Fi credential
  management — out of scope; this repo only ever reads existing state.

## Command contract: the `pulse` capability (DEV-10)

Both commissioned nodes are physical garage door opener relays and shall
only ever be pulsed (0.5s on, then off), never left on. This capability
issues Matter's native OnOff cluster `OnWithTimedOff` command — designed
for exactly this "turn on for N deciseconds then auto-off" use case — so
the device's own firmware performs the timed transition; a dropped
connection after send cannot leave the relay stuck on, unlike client-side
On/sleep/Off timing.

Verified, not guessed:
- Both nodes' OnOff cluster (`1/6/*`) `AcceptedCommandList` attribute
  (`1/6/65529`) was read live on 2026-09-30 and is `[64, 65, 66, 0, 1, 2]`
  for both node_id 1 and node_id 3 — decimal `66` (`0x42`) is
  `OnWithTimedOff`, confirming both relays support it natively.
- The matter-server's WebSocket command name/shape for issuing any device
  command was read from the real `home-assistant-libs/python-matter-server`
  client source (`matter_server/client/client.py`'s `send_device_command`
  and `matter_server/common/models.py`'s `APICommand.DEVICE_COMMAND`), not
  guessed or reverse-engineered by trial: the client sends
  `{"message_id": ..., "command": "device_command", "args": {"node_id":
  ..., "endpoint_id": ..., "cluster_id": ..., "command_name": ...,
  "payload": {...}}}` and awaits a `SuccessResultMessage`/
  `ErrorResultMessage` keyed by the same `message_id`, exactly like
  `get_nodes`.
- `OnWithTimedOff`'s fields (from the official Matter/CHIP SDK,
  `src/controller/python/chip/clusters/Objects.py`, and its Python test
  usage in `connectedhomeip`) are `onOffControl` (bitmap8, `0` = normal),
  `onTime` (uint16, **deciseconds**), and `offWaitTime` (uint16,
  deciseconds — a device-side guard period after turning off during which
  it will not accept another On command).

Message this adapter sends for a pulse (`poller/matter_client.send_pulse`):

```json
{
  "message_id": "device_command",
  "command": "device_command",
  "args": {
    "node_id": 1,
    "endpoint_id": 1,
    "cluster_id": 6,
    "command_name": "OnWithTimedOff",
    "payload": {"onOffControl": 0, "onTime": 5, "offWaitTime": 5}
  }
}
```

`onTime=5` and `offWaitTime=5` are both fixed at 5 deciseconds (0.5s),
matching the "itching mode" requirement; this capability takes no
user-supplied parameters (`catalog.py`'s `CapabilityEntry("pulse", {})`
has an empty schema) — the duration is not configurable from the
dashboard.

This has been verified read-only against the real server (the
`AcceptedCommandList` inspection above) and deployed; the first real
pulse against physical hardware is the developer's to trigger.

Dashboard presentation (2026-09-30 UX pass): the button is labeled **OPEN**
(not "pulse") and requires no confirmation popup — one tap enqueues the
request immediately, matching a garage-remote UX; the enqueue-side
cooldown and `status=healthy` precondition (DEV-10) are the safety net
instead of a blocking dialog. Each device's card shows its configured
display label (`config.yaml`'s optional `label` field) instead of its
internal id — `sonoff-1` = "GARAGE-RIGHT", `sonoff-2` = "GARAGE-LEFT".


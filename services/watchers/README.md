# Service: watchers

**Purpose.** Per-office status-watcher framework. Pluggable adapters detect office actions /
status changes and raise events consumed by docketing and the orchestrator.

**Consumers.** A1 (status watchers), A3 (rule-change monitor). Adapters: CIPO, USPTO Patent Center
+ ODP, TSDR, EPO OPS, WIPO.

**Interface sketch (implemented WP 6.2–6.4, Track A core).**
- `poll(office_adapter) -> events[]` — normalized office events enqueued to `ops.events`.
- Each adapter: auth (Bitwarden creds), fetch, diff-against-last, emit.
- Verify at build whether any office has since exposed a submission/status API (🔎 spec flag).

**Reuse.** The existing CIPO watcher (repo/VPS access via James, WP 0.6) is the first adapter to
port (WP 6.2). Audit it before porting.

**WP 0.7 status:** skeleton README only. No implementation.

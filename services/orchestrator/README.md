# Service: orchestrator (A0)

**Purpose.** The agent control plane: approval queue, egress gate (nothing sent without human
sign-off), credential brokering (Bitwarden), and invocation flows ("file X", "draft Y"). Every
outbound action from any agent passes through here.

**Consumers.** All agents route proposed actions through the approval queue + egress gate. The
universal outbound audit queue (§12.2) lives here.

**Interface sketch (implemented WP 6.1, Track A core).**
- `submit(proposal)` — enqueue an agent-proposed action for human review.
- `approve(id)` / `reject(id)` — human decision; approval releases the egress gate.
- `egress(action)` — the single choke point for anything leaving the system (email, filing, API).
- Bitwarden credential broker for worker/agent auth.

**Reuse (reference only).** The owner's personal `brunet-os` has an orchestrator + approval `state/`
queue pattern worth studying — different domain, do not import wholesale (owner ruling 2026-07-11).

**WP 0.7 status:** skeleton README only. No implementation.

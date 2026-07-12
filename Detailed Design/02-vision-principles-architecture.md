# Vision, Principles & Architecture

## 1. Vision & Objectives

A unified OS that:
1. **Runs the daily practice** — docketing, deadlines, tasks, time & billing, expenses, documents, email, CRM — replacing AppColl as the system of record.
2. **Embeds EOS discipline** — scorecards, Rocks, issues, accountability, L10 meeting cadence — natively, not bolted on.
3. **Automates** status monitoring, task generation, expense reconciliation, invoicing, reporting letters, intake, annuity instructions, and content publishing.
4. **Is agent-native and extensible** — nineteen purpose-built agents (see `06-agent-roster-A0-A18.md`), all operating under a human-approves-everything-sent policy.
5. **Integrates with M365 and Xero** rather than reinventing document storage or accounting.

**Success criteria** (the definition of "done" for v1):
- Zero missed deadlines.
- 100% of official-fee spend matter-linked and invoiced at actual settled cost (no markup, no estimate-vs-actual drift).
- Email-to-work-item automation, with human approval at every step.
- A single pane of information per matter.
- An auto-populated weekly EOS scorecard requiring no manual data assembly.
- A validated, tested AppColl cutover (not a "flip the switch and hope" migration).

## 2. Principles & Constraints

- **Integrate-first.** Teams for chat, Outlook for email transport, SharePoint for storage, Xero for the ledger, MailChimp for sending, DocuSign for signatures, Clarivate for annuity payment execution, WordPress for the public site. The OS builds the *connective tissue* between these systems — it does not try to replace utilities the firm already trusts.
- **Family is the spine.** Every other structure — matters, file references, the SharePoint folder tree, and the eventual blockchain record — hangs off the Technology Family as the top-level organizing unit. This is the single most important architectural commitment in the whole project (see below).
- **Human-in-the-loop, always, in v1.** Agents draft and propose; a human approves anything that gets sent, filed, invoiced, instructed, or posted publicly. This is explicit and non-negotiable for A8 (email instruction detection never auto-sends a Terms of Engagement or a retainer request) and for annuity payment instructions. Some future relaxation is designed for (e.g., the dormant `auto_remind` flag on reminder sending) but nothing ships pre-relaxed.
- **Auditability.** Rules are versioned. Agent actions and docket changes are logged immutably.
- **Confidentiality.** Client data is sandboxed; LLM egress is encrypted, with Zero Data Retention terms used where the provider offers them; all secrets live in Bitwarden, fetched at runtime, never stored in code or config.
- **Multi-currency by design.** CAD is the base currency; USD/EUR/CHF are routine. Client re-billing always uses the actual settled FX rate, never an estimate (this ties directly to the billing policy in D12).
- **Bilingual awareness.** CIPO correspondence and underlying data may arrive in French; the system needs to handle this gracefully rather than assume English-only content.

## 3. Architecture

**Shape:** a monorepo containing a **FastAPI (Python)** API, a set of **event-driven workers** (email arrival triggers classification/detection; a status change triggers docketing; a payment webhook triggers trust application; scheduled jobs poll and refresh external data), an **agent layer** built on the Claude API, a **React/TypeScript single-page application** for the frontend, and **Supabase Postgres** as the database, with row-level security enforcing family-level access permissions directly in the data layer rather than only in application code.

Authentication is via **Entra ID SSO** (the firm's existing Microsoft 365 identity provider). API contracts are typed and generated from an OpenAPI specification, so frontend and backend stay in sync automatically rather than drifting. Each external system (Xero, SharePoint, DocuSign, Clarivate, CIPO, USPTO, etc.) gets its own integration adapter, and every adapter fetches its credentials from Bitwarden at runtime — nothing is ever hard-coded or stored in application config.

### 3.1 Data-Access Abstraction & the Blockchain Fork

This is the architectural answer to D3 (blockchain treated as a future fork, not a v1 build item), and it's worth spelling out because it's the mechanism that makes "build for the future without building the future" actually work rather than being a hand-wave.

The design uses a **repository pattern** — a `FamilyRecordStore` abstraction that sits between the application and wherever family data actually lives. In v1, the concrete implementation writes to Supabase Postgres like everything else. But every family's canonical bibliographic and status data is also expressed as a **versioned, canonical Family Record JSON schema**: family ID, title, applicant/owner, and a per-country record (country, application/registration numbers, key dates, status, and the responsible attorney or associate) for every jurisdiction the family touches.

On top of that schema sits a **family-level access-control model** — the applicant holds ultimate authority, the initiating attorney is a delegate, and per-country associates get scoped read grants — enforced today entirely in-application (via Supabase RLS), but designed so the same permission model could later be enforced cryptographically on a permissioned chain without a redesign.

Finally, there's a **signed `FamilyRecordExport` API** — a way to produce a verifiable, shareable snapshot of a family record. This is useful *immediately*, for sharing data with foreign associates in a controlled way, and it's designed to double as the future chain-write path when the blockchain fork actually happens. It's also, not coincidentally, the same API that the deferred Client Portal (D34) is planned to be built on top of — one piece of infrastructure serving two future needs.

**What stays off-chain permanently, by design:** operational data — time entries, billing details, email content, EOS scorecard data. Only the family's core bibliographic/status record is a chain candidate; the day-to-day operational exhaust of running the firm never was and never will be.

The blockchain build itself is tracked as a separate, explicitly future item (Tracker item F.1) — nothing in the v1 plan depends on it existing.

# Modules M5–M8: Documents, Email, CRM/Intake, Marketing

---

## M5 — Documents (SharePoint/M365)

SharePoint is the master document store; the existing OneDrive mirror is left untouched, and the OS **always writes to SharePoint** — never to a separate storage layer that would create a second source of truth. The matter-to-folder mapping follows Appendix A exactly (`07-appendix-A-file-conventions.md`), now validated against the actual live tenant structure (see D36 in the discovery-findings file).

Documents open for editing via the Office for the Web/desktop URL, so saves stay native to Office rather than round-tripping through the OS. 🔎 One confirmed technical constraint from research: Microsoft Graph does not support inline editable Office embedding — the editor opens in a new tab rather than embedding in the OS's own UI. This is a minor UX accommodation, not a blocker.

Generated, downloaded, or executed documents are auto-filed following the naming convention `YYYY-MM-DD-Description.ext` into the correct subfolder — `FH/Received`, `FH/Sent`, `Correspondence`, or `Assignment`, matching the folder taxonomy confirmed by the SharePoint crawl. Files added externally (i.e., not through the OS) are picked up via Microsoft Graph delta queries and webhooks, so the OS's index of what exists stays current without anyone needing to manually sync. All file types are supported, not just Office documents.

---

## M6 — Email Management

**Scope (D15):** the four shared mailboxes — patents@ (the CIPO correspondence address, and presumed to also be the USPTO Patent Center notification target), trademarks@, accounting@ (billing triage inbound, invoice sending outbound), and info@ (the CRM intake channel, currently fed by HubSpot from website forms) — **plus every individual team member's mailbox**, via per-user Microsoft Graph subscriptions.

- **M6-R1:** Ingestion happens via Graph webhooks. Sent items are auto-filed too, especially replies, so a thread stays continuous in the OS regardless of which direction each message went.
- **M6-R2:** AI-driven matter classification, with the file-reference regex pattern as the strongest signal, backed up by sender/recipient contact matching and content analysis. Classification confidence is thresholded, and anything below the threshold lands in a triage queue for a human to resolve rather than being silently misfiled.
- **M6-R3:** A full email database — threaded, deduplicated, full-text searchable — linked to matters, with attachments optionally auto-filed per M5's rules.
- **M6-R4:** A unified correspondence timeline per matter, so the full history of communication about a matter is visible in one place regardless of who sent or received each message.
- **M6-R5:** A template library, with approved outbound messages sent via Graph from the *correct* mailbox automatically (e.g., invoices always go out from accounting@, never from a personal mailbox). Sending a message can also close or advance a workflow task, closing the loop between communication and work tracking.
- **M6-R6 — Legacy migration:** an estimated 10,000–15,000 `.msg` files get parsed out of SharePoint, matching the observed filename convention `YYYY-MM-DD__{From}to{To}__{Subject}.msg`, cross-referenced against the `.msg` file's own internal headers, deduplicated against whatever's already in the live mailboxes, and loaded into the new email database — with the matter link inferred from **where the file physically sat in the folder tree**, since the folder location is treated as ground truth for matter assignment during this migration.

---

## M7 — CRM, Intake & Reciprocity

Covers the sales pipeline, quoting, discount schedules, LinkedIn outreach (manual-assist only, per D16 — the OS drafts and tracks, a human clicks send), and quote intake via website forms (post-HubSpot, these become OS-served forms directly, with a copy still delivered to info@ as a mailbox-level backup in case the form pipeline ever has an issue).

**The intake chain** is designed as one tracked pipeline, start to finish: a quote gets accepted → a conflicts check runs automatically → a Terms of Engagement document goes out via DocuSign (generated from the firm's own Word template, data-merged from the OS) → a retainer request goes out as a Xero invoice carrying a card-payment link, coded to the trust liability account → the payment webhook fires, marking the retainer received and updating the trust balance → the family and matter get opened → and a work item enters the workflow system to actually start the engagement. Agent A8 (email instruction detection) can *initiate* this chain from an inbound client email, but every step still passes through its normal human-approval gate before the ToE or retainer request actually goes out.

**Signature workflows are generalized** beyond just the ToE: DocuSign handles ToE envelopes and USPTO declaration/oath/POA forms as a matter of course. For assignments specifically, the choice is made per-envelope between DocuSign and a tracked wet-signature path (document sent for physical execution → executed copy received back → filed) — because some European patent offices still require wet-ink signatures on assignments. The system's default behavior is to ask which path is needed rather than assuming.

**Reciprocity tracking:** inbound and outbound referral records are kept at the family-plus-matter level. Madrid Protocol designations of Canada are specifically counted as inbound referrals from the instructing foreign associate, which matters for keeping the reciprocity ratios (overall, by matter type, by period) accurate — a detail that would otherwise be easy to miscount if Madrid designations were treated like ordinary direct-filed matters.

**Newsletter, via MailChimp (D16):** the OS masters the actual mailing lists and content; audience segments and tags sync in both directions; campaigns push out through MailChimp; opens, clicks, and unsubscribes pull back into the OS. CASL consent fields — basis, date, source — live on every contact the firm might reach via marketing email, and unsubscribe state is kept in sync bidirectionally between the two systems.

**HubSpot exit:** a one-time import of contacts, companies, deals, and notes, paired with repointing the website's form embeds away from HubSpot and onto the OS's own forms (a template edit on the custom WordPress theme).

### The Opposition Watch module (D25)

This module has two halves, both feeding Agent A14:

**(a) Watchlist mode:** client marks — and any third-party marks a client specifically asks the firm to monitor — are registered for watching, each with its own screening criteria (word elements, phonetic variants, Nice classification, goods-and-services overlap). A hit against a newly advertised mark generates a client alert, a report drawn from a template, and — critically — an automatically **docketed opposition deadline** (2 months from the advertisement date, with extension logic built in) that flows through the M1 deadline engine like any other docket item.

**(b) Prospect ledger mode:** the flip side — non-client owners of *earlier* marks that A14 identifies as being conflicted by someone else's newly advertised mark. Each prospect record carries the conflicting mark, the similarity rationale that flagged it, the opposition deadline, contact-enrichment results, the CASL consent basis for outreach, outreach status, and eventual conversion outcome. Because this whole mode is deadline-driven, every prospect record carries an expiry, and outreach tasks auto-cancel the moment the opposition window closes — there's no value (and real compliance risk) in still messaging someone about a window that's shut.

---

## M8 — Marketing & Content

A content calendar and library with an approvals workflow, generation agents to draft content, and multi-channel publishing: the **WordPress REST API** (Kinsta-hosted, custom template, authenticated via application password or OAuth, supporting both drafts and scheduled posts, with Kinsta's staging environment used to test the connector before it ever touches production), plus the LinkedIn organic-posting API and the YouTube Data API. Engagement metrics from every published item feed directly into the EOS scorecard (M9), so content performance is visible in the same weekly accountability rhythm as everything else — it doesn't live in a separate marketing-only dashboard nobody checks. Per D16, the OS is designed to own all content and all prospect lists outright, rather than treating marketing data as something that lives primarily in an external tool.

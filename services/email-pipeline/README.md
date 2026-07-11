# Service: email-pipeline

**Purpose.** Ingest, thread, classify, and file email across the 4 shared mailboxes + all user
mailboxes. The substrate for every email-derived workflow. (M6; feeds A8/A9/A10.)

**Consumers.** A8 (instruction detection), A9 (drafting), A10 (quote detection), M6 correspondence
timeline, the M5-R filing provenance log.

**Interface sketch (implemented WP 4.3–4.4, not here).**
- `ingest(mailbox, since_delta)` — Graph delta-query pull; subscription manager renews webhooks
  (~3-day expiry) with a catch-up sweep; throttle-aware backfill for 14-mailbox history.
- `classify(message) -> {matter_id?, confidence}` — precision-checked before go-live.
- `file(message, matter_ids[], primary)` — many-to-many (M5-R2); logs suggested-vs-selected pair.
- Mailbox privacy enforced in RLS (D39/M6-R7): unlinked messages visible only to the owner.

**Reuse.** `email-assistant/src/{ingest,thread,clean,redactor}.py` is a working pipeline to port.

**WP 0.7 status:** skeleton README only. No implementation.

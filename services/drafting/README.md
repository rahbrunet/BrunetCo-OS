# Service: drafting

**Purpose.** Style-trained, KB-grounded text generation with a human review queue. One engine,
many callers — each "agent" is a config + prompt + corpus on top.

**Consumers.** A9 (per-user email drafters), A11 (OA reporting drafter), A18 (reminder/dunning
templates), M8 (marketing content).

**Interface sketch (implemented WP 6.9/6.11, not here).**
- `draft(context, style_profile, kb_refs) -> DraftProposal` — never sends; lands in the review queue.
- `train_style(user_id, sent_corpus)` — per-user voice from sent mail.
- KB grounding via the knowledge-base service; citations attached to every draft.

**Reuse.** `email-assistant/src/{draft,draft_feedback,embed,corpus_updater,response_validator}.py`
is the A9 template (WP 6.9 gating code — found on-machine). Replace Chroma with pgvector
(design review §6.3).

**WP 0.7 status:** skeleton README only. No implementation.

"""Reference generation — the Appendix A grammar, in code (M1-R1).

Two layers:

* **Pure grammar** (`next_segment`, `family_reference`, `matter_reference`,
  `family_display_reference`) — no I/O, exhaustively unit-testable. This is where the ordered
  jurisdiction-segment rule lives: ``USP → US → US2 → US3 …``, with ``PCT`` and ``MP`` as
  ordinary sibling segments (never parents encoded into the string).
* **DB-aware allocators** (`allocate_family_seq`, `generate_family_reference`,
  `generate_matter_reference`) — run on the caller's RLS-scoped connection (D44) and read the
  current state to pick the next sequence number. They own no credentials.

Invariants enforced here (Appendix A / M1-R1):

* The reference string NEVER encodes ``relationship_type``. Continuation/CIP/divisional and
  PCT/Madrid national-phase links live in ``parent_matter_id`` + ``relationship_type`` only.
* The TM/Design tag is a **display** concern, not part of the stored, unique ``reference``
  column — the raw reference is already unique on ``{client}-{seq}[-{segment}]``, and tagging
  the stored string would risk encoding matter-type-ish data into an identity key. See
  `family_display_reference`.
* Per-client numbering schemes are honored via ``clients.reference_scheme`` (a template with
  ``{client}`` / ``{seq}`` placeholders); ``null`` means the standard 4-digit sequence.
"""
from __future__ import annotations

import re
from uuid import UUID

import psycopg

DEFAULT_FAMILY_SCHEME = "{client}-{seq}"
# Per-client general/advisory catch-all (Appendix A) — never auto-issued.
GENERAL_FAMILY_SEQ = "9999"
_STANDARD_SEQ = re.compile(r"^\d{4}$")


# --- Pure grammar ----------------------------------------------------------


def family_reference(client_code: str, family_seq: str, scheme: str | None = None) -> str:
    """``{ClientCode}-{FamilySeq}`` — or a per-client scheme template if one is set."""
    return (scheme or DEFAULT_FAMILY_SCHEME).format(client=client_code, seq=family_seq)


def matter_reference(family_ref: str, segment: str) -> str:
    """``{FamilyRef}-{JurisdictionSegment}``; a no-jurisdiction (advisory) matter has no segment
    and reuses the family reference unchanged (Appendix A)."""
    return f"{family_ref}-{segment}" if segment else family_ref


def family_display_reference(reference: str, family_type: str, tm_design: bool) -> str:
    """Reference with the trademark tag applied for display (Appendix A).

    Trademark families carry a tag after the sequence: ``Design`` for a design mark,
    ``(TM)`` for a standard-character (word) mark. Patents, industrial designs, and advisory
    families are left untagged. The tag is display-only — it is not stored in ``reference``.
    """
    if family_type == "trademark":
        return f"{reference} Design" if tm_design else f"{reference} (TM)"
    return reference


def next_segment(base: str, existing_segments: list[str]) -> str:
    """Next segment in the ordered per-family sequence for a jurisdiction ``base``.

    ``base`` is the bare jurisdiction/vehicle code as it appears at the start of a segment:
    ``US``, ``USP``, ``CA``, ``EP``, ``PCT``, ``MP`` … The sequence runs ``base`` (bare, = 1),
    then ``base2``, ``base3``, … matching live production (``US11``, ``CA5``, ``EP2``).

    Sequences are independent per base: ``USP`` (provisional) is its own track and does NOT
    consume a number in the regular ``US`` sequence — ``USP`` then ``US`` then ``US2``.
    ``PCT`` and ``MP`` are just their own bases (siblings), so their national designations get
    ordinary country segments and are linked back only via ``parent_matter_id``.
    """
    matcher = re.compile(rf"^{re.escape(base)}(\d*)$")
    highest = 0
    for seg in existing_segments:
        m = matcher.match(seg)
        if m is None:
            continue
        index = int(m.group(1)) if m.group(1) else 1
        highest = max(highest, index)
    nxt = highest + 1
    return base if nxt == 1 else f"{base}{nxt}"


# --- DB-aware allocators (run on the caller's RLS-scoped connection) --------


def _client_meta(conn: psycopg.Connection, client_id: UUID) -> tuple[str, str | None]:
    row = conn.execute(
        "select code, reference_scheme from app.clients where id = %s", (client_id,)
    ).fetchone()
    if row is None:
        raise LookupError("client not found or not visible")
    return row[0], row[1]


def allocate_family_seq(conn: psycopg.Connection, client_id: UUID) -> str:
    """Next standard 4-digit family sequence for a client.

    Only standard ``NNNN`` sequences are considered (per-client custom schemes and the ``9999``
    general catch-all are skipped), so an auto-issued seq never collides with those. Racy under
    concurrency by design — the ``families.unique(client_id, family_seq)`` constraint is the real
    guard, surfaced to the caller as a 409.
    """
    rows = conn.execute(
        "select family_seq from app.families where client_id = %s", (client_id,)
    ).fetchall()
    highest = 0
    for (seq,) in rows:
        if _STANDARD_SEQ.match(seq) and seq != GENERAL_FAMILY_SEQ:
            highest = max(highest, int(seq))
    return f"{highest + 1:04d}"


def generate_family_reference(
    conn: psycopg.Connection, client_id: UUID, family_seq: str | None = None
) -> tuple[str, str]:
    """Return ``(family_seq, reference)`` for a new family under ``client_id``.

    ``family_seq`` is allocated automatically when not supplied. The per-client
    ``reference_scheme`` template (if any) shapes the reference string.
    """
    code, scheme = _client_meta(conn, client_id)
    seq = family_seq if family_seq is not None else allocate_family_seq(conn, client_id)
    return seq, family_reference(code, seq, scheme)


def generate_matter_reference(
    conn: psycopg.Connection, family_id: UUID, segment_base: str
) -> tuple[str, str]:
    """Return ``(segment, reference)`` for a new matter under ``family_id`` in ``segment_base``.

    The segment is the next in the ordered per-family sequence for that base (``US`` → ``US2`` …).
    Pass an empty ``segment_base`` for a no-jurisdiction advisory matter.
    """
    fam = conn.execute(
        "select reference from app.families where id = %s", (family_id,)
    ).fetchone()
    if fam is None:
        raise LookupError("family not found or not visible")
    family_ref = fam[0]
    if not segment_base:
        return "", matter_reference(family_ref, "")
    existing = [
        r[0]
        for r in conn.execute(
            "select jurisdiction_segment from app.matters where family_id = %s", (family_id,)
        ).fetchall()
    ]
    segment = next_segment(segment_base, existing)
    return segment, matter_reference(family_ref, segment)

"""WP 0.8 RLS acceptance tests — direct-Postgres proofs of the D43/D39 policy framework.

Same pattern as test_rls.py (the D44 proof) and the pattern WP 9.1 acceptance tests reuse:
Postgres itself — not app code — must enforce:
  1. family visibility (restricted families visible only via ACL / admin),
  2. D43 domain gating (non-admins cannot write permission grants),
  3. the own-record rule (users see their own grants; admins see all),
  4. D39 mailbox privacy (unlinked email owner-only; matter-linked firm-visible;
     shared-mailbox firm-visible).

Setup uses the superuser connection (test fixture = the enumerated migrations/system path);
assertions run through user JWTs only.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from py_shared.auth import EntraIdentity, mint_supabase_jwt, user_connection
from py_shared.config import settings


def _db_ready() -> bool:
    try:
        with psycopg.connect(settings.supabase_db_url, connect_timeout=3) as conn:
            row = conn.execute("select to_regclass('app.emails')").fetchone()
            return row is not None and row[0] is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_ready(), reason="Postgres with WP 0.8 migrations not reachable"
)


class Ctx:
    admin_id: str
    staff_id: str
    other_id: str
    admin_jwt: str
    staff_jwt: str
    other_jwt: str
    family_open: str
    family_restricted: str
    matter_open: str
    email_unlinked: str
    email_linked: str
    email_shared: str


@pytest.fixture(scope="module")
def ctx() -> Iterator[Ctx]:
    c = Ctx()
    c.admin_id, c.staff_id, c.other_id = (str(uuid.uuid4()) for _ in range(3))
    ids = ((c.admin_id, "admin_jwt"), (c.staff_id, "staff_jwt"), (c.other_id, "other_jwt"))
    for uid, attr in ids:
        identity = EntraIdentity(os_user_id=uid, email=f"{uid[:8]}@t.local")
        setattr(c, attr, mint_supabase_jwt(identity))

    with psycopg.connect(settings.supabase_db_url, autocommit=True) as su:
        su.execute(
            """
            insert into app.os_users (id, email, display_name, role_template) values
              (%s, %s, 'T Admin', 'Principal'),
              (%s, %s, 'T Staff', 'Agent'),
              (%s, %s, 'T Other', 'Agent')
            """,
            (c.admin_id, f"a-{c.admin_id[:8]}@t.local",
             c.staff_id, f"s-{c.staff_id[:8]}@t.local",
             c.other_id, f"o-{c.other_id[:8]}@t.local"),
        )
        su.execute(
            """
            insert into app.permission_grants (user_id, domain, granted_by)
            values (%s, 'compensation_admin', %s)
            """,
            (c.admin_id, c.admin_id),
        )
        client_id = su.execute(
            "insert into app.clients (code, name) values (%s, 'Test Co') returning id",
            (f"T{uuid.uuid4().hex[:5].upper()}",),
        ).fetchone()[0]
        c.family_open = str(su.execute(
            """
            insert into app.families (client_id, family_seq, reference, title, family_type)
            values (%s, '0001', %s, 'Open Tech', 'patent') returning id
            """,
            (client_id, f"T-{uuid.uuid4().hex[:8]}"),
        ).fetchone()[0])
        c.family_restricted = str(su.execute(
            """
            insert into app.families
              (client_id, family_seq, reference, title, family_type, restricted)
            values (%s, '0002', %s, 'Secret Tech', 'patent', true) returning id
            """,
            (client_id, f"T-{uuid.uuid4().hex[:8]}"),
        ).fetchone()[0])
        # ACL: staff (not other) may see the restricted family.
        su.execute(
            """
            insert into app.family_access (family_id, user_id, access_level, granted_by)
            values (%s, %s, 'delegate', %s)
            """,
            (c.family_restricted, c.staff_id, c.admin_id),
        )
        c.matter_open = str(su.execute(
            """
            insert into app.matters (family_id, reference, jurisdiction_code, jurisdiction_segment)
            values (%s, %s, 'CA', 'CA') returning id
            """,
            (c.family_open, f"T-{uuid.uuid4().hex[:8]}-CA"),
        ).fetchone()[0])
        # Emails: unlinked personal (owner = staff), matter-linked personal, shared-mailbox.
        insert_email = (
            "insert into app.emails (mailbox_owner_id, subject) values (%s, %s) returning id"
        )
        c.email_unlinked = str(su.execute(insert_email, (c.staff_id, "private")).fetchone()[0])
        c.email_linked = str(
            su.execute(insert_email, (c.staff_id, "about CA matter")).fetchone()[0]
        )
        su.execute(
            "insert into app.email_matter_links (email_id, matter_id, linked_by)"
            " values (%s, %s, %s)",
            (c.email_linked, c.matter_open, c.staff_id),
        )
        c.email_shared = str(su.execute(insert_email, (None, "shared inbox")).fetchone()[0])

    yield c


def _visible_families(jwt: str) -> set[str]:
    with user_connection(jwt) as conn:
        return {str(r[0]) for r in conn.execute("select id from app.families").fetchall()}


def _visible_emails(jwt: str) -> set[str]:
    with user_connection(jwt) as conn:
        return {str(r[0]) for r in conn.execute("select id from app.emails").fetchall()}


# --- 1. Family visibility -------------------------------------------------

def test_open_family_visible_to_all_staff(ctx: Ctx) -> None:
    for jwt in (ctx.staff_jwt, ctx.other_jwt, ctx.admin_jwt):
        assert ctx.family_open in _visible_families(jwt)


def test_restricted_family_only_via_acl_or_admin(ctx: Ctx) -> None:
    assert ctx.family_restricted in _visible_families(ctx.staff_jwt)   # ACL delegate
    assert ctx.family_restricted not in _visible_families(ctx.other_jwt)
    assert ctx.family_restricted in _visible_families(ctx.admin_jwt)   # permissions admin


# --- 2. D43 domain gating on grant writes ----------------------------------

def test_non_admin_cannot_write_grants(ctx: Ctx) -> None:
    with pytest.raises(psycopg.Error):
        with user_connection(ctx.other_jwt) as conn:
            conn.execute(
                """
                insert into app.permission_grants (user_id, domain, granted_by)
                values (%s, 'accounting_reporting', %s)
                """,
                (ctx.other_id, ctx.other_id),
            )


def test_admin_can_grant_and_revoke(ctx: Ctx) -> None:
    with user_connection(ctx.admin_jwt) as conn:
        conn.execute(
            """
            insert into app.permission_grants (user_id, domain, granted_by)
            values (%s, 'time_entry', %s)
            """,
            (ctx.other_id, ctx.admin_id),
        )
    with user_connection(ctx.admin_jwt) as conn:
        conn.execute(
            "delete from app.permission_grants where user_id = %s and domain = 'time_entry'",
            (ctx.other_id,),
        )


# --- 3. Own-record rule on grants ------------------------------------------

def test_user_sees_own_grants_only(ctx: Ctx) -> None:
    with user_connection(ctx.staff_jwt) as conn:
        owners = {str(r[0]) for r in conn.execute(
            "select distinct user_id from app.permission_grants"
        ).fetchall()}
    assert owners <= {ctx.staff_id}  # nobody else's grants visible


def test_admin_sees_all_grants(ctx: Ctx) -> None:
    with user_connection(ctx.admin_jwt) as conn:
        owners = {str(r[0]) for r in conn.execute(
            "select distinct user_id from app.permission_grants"
        ).fetchall()}
    assert ctx.admin_id in owners


# --- 4. D39 mailbox privacy --------------------------------------------------

def test_unlinked_email_owner_only(ctx: Ctx) -> None:
    assert ctx.email_unlinked in _visible_emails(ctx.staff_jwt)
    assert ctx.email_unlinked not in _visible_emails(ctx.other_jwt)
    assert ctx.email_unlinked not in _visible_emails(ctx.admin_jwt)  # admin ≠ mail snoop


def test_matter_linked_email_firm_visible(ctx: Ctx) -> None:
    assert ctx.email_linked in _visible_emails(ctx.other_jwt)


def test_shared_mailbox_email_firm_visible(ctx: Ctx) -> None:
    assert ctx.email_shared in _visible_emails(ctx.other_jwt)

# Data Residency — decide before locking a hosting region (design review §6.5)

**Out of scope for WP 0.7** (no hosting selection here) but flagged so it is a conscious decision,
not a default.

D2 accepts Canada-or-US hosting. Generally fine under PIPEDA, but the data model carries a US
Government agency/contract field and IPON-funded clients — some engagement letters or
government-client contracts may impose residency or handling constraints.

**Action before locking a US region:** one-time scan of standard engagement terms for residency /
data-handling clauses. Record the outcome here and in `DECISIONS.md`.

Related (design review §6.4): decide consciously on a third-party M365 backup — SharePoint/Exchange
native retention is not a restore product, and SharePoint holds the entire matter file.

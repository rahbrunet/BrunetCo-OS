# How We Edit These Files

This repo is now the source of truth for BrunetCo OS design and specification documentation, replacing the OneDrive/SharePoint copies. Please stop editing the OneDrive files directly — continuing to do so will cause the two copies to drift apart, which defeats the point of moving here.

## Where things live

- `Design Summary/` — the two canonical, cumulative documents (`IP-OS-SPEC-v0.15.md`, `IP-OS-TRACKER.md`) that get pinned to Claude Project knowledge. Dense, table-heavy, kept for cross-reference.
- `Detailed Design/` — the same content unpacked into readable, numbered files (00 README through 13). Use these for day-to-day reading and editing.
- `README.md` — repo landing page.

## Making a small edit (typo, one section, a status update)

1. Open the file on GitHub and click the pencil (edit) icon.
2. Make your change.
3. Write a short, specific commit message describing what changed and why (e.g. "Mark WP 0.7 in progress", not "update").
4. Commit directly to `main`.

If someone else changed the file since you opened it, GitHub will warn you before you overwrite their edit — this is the main thing SharePoint couldn't do for us.

## Making a larger edit (new decision, restructuring a module, adding a work package)

1. On the edit screen, choose "Create a new branch for this commit and start a pull request" instead of committing straight to `main`.
2. Open the pull request and tag whoever else touches that area for a quick look.
3. Merge once reviewed.

## Conventions to keep

- Log new decisions in `Detailed Design/01-decision-register.md` (next Dxx number) and reflect them in `11-build-tracker.md`.
- If a change is significant enough to affect the canonical `Design Summary/` docs, update both places in the same commit or PR so they don't diverge.
- Keep the numbered file order in `Detailed Design/` intact — several files cross-reference each other by filename.

## Who to ping

Rob owns the spec content and has final say on structure. Check with him before renaming or moving files between folders, since some docs link to each other by path.

# CI definitions

`ci.yml` here is the canonical, reviewed copy of the pipeline. GitHub Actions only executes
workflows under `.github/workflows/`, so a live copy lives at `.github/workflows/ci.yml`. Keep the
two in sync — edit here, then copy to `.github/workflows/`. (A future `make ci-sync` target can
enforce this.)

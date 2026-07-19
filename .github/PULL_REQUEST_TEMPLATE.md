<!--
Title format: <type>(OGE-XXX): short summary
  e.g. feat(OGE-491): policy engine — sensitivity tiers + fail-closed resolution

Allowed types: feat, fix, docs, refactor, test, chore, perf, build, ci, revert
Conventional Commits is enforced — see .commitlintrc.json.

The `## UAT checklist` block is REQUIRED. OgenticAI Reviewer parses it and posts
per-item PASS / FAIL / PARTIAL / UNVERIFIABLE verdicts on the PR.
-->

Closes [OGE-XXX](https://linear.app/ogenticai/issue/OGE-XXX). <!-- one line on where this stands -->

## What changed

<!-- The user-visible diff. What does a reader of the changelog need to know? -->

## How it works

<!-- The implementation. Mention any non-obvious invariant, ordering, or failure path. -->

## Files

<!-- Group by area. Skip if the file list is small and self-explanatory. -->

## Privacy-invariant impact

<!--
See CONTRIBUTING.md § "Privacy invariants" and docs/adr/0001-router-architecture.md.
  - [ ] No change to classification, policy resolution, redaction, or dispatch.
  - [ ] Changes one of them — explain below which invariant is affected and how it still holds.
-->

- [ ] Sensitive content still cannot resolve to a non-local backend (fail-closed, including failover order).
- [ ] Classifier / policy / audit failures refuse the request rather than proceeding.
- [ ] Audit rows remain shape-only — no raw prompt text, no redaction mapping.
- [ ] Backend selection stays within the policy allow-list; no caller-supplied URL can redirect dispatch.
- [ ] Backend credentials resolve by reference; none are logged, echoed in errors, or written to an audit row.

## Verified locally

- [ ] `.venv/bin/ruff check src/ tests/` — clean
- [ ] `.venv/bin/mypy src/` — clean
- [ ] `.venv/bin/pytest tests/` — N passing

## UAT checklist

<!-- One verifiable claim per line. These are what the Reviewer grades — be specific. -->

- [ ] Item 1 spelling out the verifiable claim
- [ ] Item 2 ...

## Reviewer notes

<!-- Anything the reviewer should look at first, or known follow-ups deferred to a separate ticket. -->

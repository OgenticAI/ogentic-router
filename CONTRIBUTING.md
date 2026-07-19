# Contributing to ogentic-router

Pre-alpha. v0.1 is in flight — see the [Linear project](https://linear.app/ogenticai/project/ogentic-router-oss-46e612b52d27) for active tickets.

## Local setup

```bash
git clone https://github.com/OgenticAI/ogentic-router
cd ogentic-router
uv venv && source .venv/bin/activate
uv pip install --python .venv/bin/python -e ".[dev]"
```

## Development loop

```bash
.venv/bin/ruff check src/ tests/   # lint
.venv/bin/mypy src/                 # types
.venv/bin/pytest tests/ -v          # tests
```

Same surface as `ogentic-shield` — if you're moving between repos, the commands match.

## Pull-request conventions

Every PR runs through the OgenticAI Reviewer, which parses a `## UAT checklist` block from the PR body and posts per-item PASS / FAIL / PARTIAL / UNVERIFIABLE verdicts. Include the block in every PR.

Example PR body shape:

```markdown
Closes [OGE-XXX](https://linear.app/ogenticai/issue/OGE-XXX).

## What changed
- ...

## How it works
- ...

## Files
- ...

## Verified locally
- `.venv/bin/ruff check src/ tests/` — clean
- `.venv/bin/mypy src/` — clean
- `.venv/bin/pytest tests/` — N passing

## UAT checklist
- [ ] Item 1 spelling out the verifiable claim
- [ ] Item 2 ...
```

`.github/PULL_REQUEST_TEMPLATE.md` pre-fills this shape — including the privacy-invariant checklist below.

Commit style: [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `perf:`, `build:`, `ci:`, `revert:`), scoped to the ticket where there is one — `feat(OGE-XXX): …`. The allowed types and a 100-character header limit are declared in [`.commitlintrc.json`](.commitlintrc.json); enforcement lands with the CI pipeline ticket. No Claude/Anthropic co-author trailers.

## Conduct and security

- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — Contributor Covenant v2.1; reports to conduct@ogenticai.com.
- [`SECURITY.md`](SECURITY.md) — **do not file security findings as public issues.** Email security@ogenticai.com. Anything that lets sensitive content reach a network backend is priority-zero.

## Privacy invariants (load-bearing — don't violate)

- **The router runs in-process on the user's machine.** No Ogentic-hosted infrastructure ever sees user content.
- **Audit events are shape-only.** Decision rows carry hashes, scores, category labels, and backend ids — never raw prompt text.
- **Backend allow-listing.** Endpoint validators must accept only the backends declared in policy. No tool input should be able to redirect to an arbitrary URL.
- **Failure-safe.** Router exceptions surface as MCP / HTTP errors; never silently swallow a sensitivity classification.
- **Local-only default for sensitive content.** The policy engine's default rule must route sensitive content to a local backend; cloud is opt-in for cleared / redacted payloads.

## License

By contributing, you agree your contributions are licensed under Apache-2.0 (see [LICENSE](LICENSE)).

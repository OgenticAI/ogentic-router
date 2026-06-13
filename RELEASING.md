# Releasing ogentic-router

The release process is automated via `.github/workflows/release.yml`. Pushing a tag like `v0.1.1` triggers the workflow which builds the sdist + wheel, runs `twine check`, uploads to PyPI, then smoke-installs from PyPI to confirm.

This document is the operator's manual. **It assumes the v0.1.0 first-publish already happened** (see §3 for that one-off). Routine releases after v0.1.0 follow §1.

---

## §1 — Cutting a routine release (v0.1.1, v0.2.0, …)

### Pre-flight (5 min)

1. **Pull main** clean:
   ```bash
   git checkout main && git pull --ff-only
   ```

2. **Bump versions** — *both* must match the tag-without-leading-v:

   | File | Bump |
   |---|---|
   | `pyproject.toml` (`[project] version = "X.Y.Z"`) | yes |
   | `src/ogentic_router/__init__.py` (`__version__ = "X.Y.Z"`) | yes |

3. **Verify** they match each other:
   ```bash
   grep '^version' pyproject.toml
   grep '__version__' src/ogentic_router/__init__.py
   ```

4. **Run the full local pipeline:**
   ```bash
   uv pip install --python .venv/bin/python -e ".[dev]"
   .venv/bin/ruff check src/ tests/
   .venv/bin/mypy src/
   .venv/bin/pytest tests/
   ```
   All four green or the release doesn't happen.

5. **Build + check locally** (catches everything CI catches, faster):
   ```bash
   rm -rf dist/
   python -m build
   python -m twine check dist/*
   unzip -l dist/*.whl | grep -E 'tests/|benchmarks/|__pycache__' && echo "FAIL: stowaway in wheel" || echo "OK"
   ```

6. **Smoke install** from the local wheel in a throwaway venv:
   ```bash
   python -m venv /tmp/pre-release-smoke
   /tmp/pre-release-smoke/bin/pip install dist/*.whl
   /tmp/pre-release-smoke/bin/python -c "import ogentic_router; print(ogentic_router.__version__)"
   rm -rf /tmp/pre-release-smoke
   ```

### Commit + tag + push

```bash
git add pyproject.toml src/ogentic_router/__init__.py
git commit -m "chore(release): vX.Y.Z"
git push

git tag vX.Y.Z
git push origin vX.Y.Z
```

The tag push fires `.github/workflows/release.yml`. Watch it:

```bash
gh run watch --repo OgenticAI/ogentic-router $(gh run list --workflow release.yml --limit 1 --json databaseId --jq '.[0].databaseId')
```

### Post-publish verification

The workflow's own smoke step verifies `pip install ogentic-router==X.Y.Z` works, but verify the user-facing path too:

```bash
python -m venv /tmp/post-release-smoke
/tmp/post-release-smoke/bin/pip install ogentic-router==X.Y.Z
/tmp/post-release-smoke/bin/python -c "import ogentic_router; print(ogentic_router.__version__)"

# And every extra:
/tmp/post-release-smoke/bin/pip install 'ogentic-router[shield]==X.Y.Z'
/tmp/post-release-smoke/bin/pip install 'ogentic-router[cloud]==X.Y.Z'
rm -rf /tmp/post-release-smoke
```

If anything fails, **yank the release immediately**:

```bash
# from a Python with twine installed and the API token in the env:
python -m twine upload --skip-existing dist/*  # NO — don't re-upload to "fix"
# instead, on PyPI's web UI:
#   1. Open https://pypi.org/manage/project/ogentic-router/releases/
#   2. "Yank" the broken version (does NOT delete; marks it as do-not-install
#      for new resolves, preserves install for users who already pinned it)
#   3. Cut X.Y.(Z+1) with the fix
```

### Update the changelog

Append release notes to `CHANGELOG.md`. Don't backfill old releases — start from the release that's adding the changelog.

---

## §2 — Pre-release versions (alpha / beta / rc)

For previewing a release without publishing it as "the" version that `pip install ogentic-router` resolves:

| Version | Effect |
|---|---|
| `0.2.0a1` | Alpha 1; `pip install ogentic-router` skips it; `pip install ogentic-router==0.2.0a1` works |
| `0.2.0b1` | Beta |
| `0.2.0rc1` | Release candidate |
| `0.2.0.dev0` | Development build; `pip install --pre ogentic-router` resolves it |

Same release flow as §1 with the pre-release suffix in both files. Use this for the first server/audit integration drops before pinning the stable.

---

## §3 — One-off: setting up the v0.1.0 first release

These are the steps for the *very first* PyPI publish; they don't repeat after v0.1.0 lands.

### 3a — PyPI account + project ownership

1. PyPI account: `davidoladejiogenticai` (personal, exists). The `ogenticai` Company organization is requested (pending PyPI staff approval, 1-3 weeks).
2. The first upload claims the `ogentic-router` project name on PyPI. Owner is whoever pushes that first upload.
3. **Transfer to org later:** once the `ogenticai` org is approved, on the PyPI project page → "Manage" → "Collaborators" → "Transfer project to organization".

### 3b — API token (first push)

1. https://pypi.org/manage/account/token/ → "Add API token".
2. Name: `OgenticAI Reviewer release workflow — ogentic-router`.
3. Scope: **"Project: ogentic-router"** (only available *after* the first upload — for the very first push, use "Entire account (all projects)" temporarily, then immediately rotate to a scoped token).
4. Copy the token (starts with `pypi-…`); you will not see it again.
5. **GitHub:** OgenticAI org → Settings → Secrets and variables → Actions → "New organization secret".
   - Name: `PYPI_API_TOKEN`
   - Value: the token from step 4
   - Repository access: select `ogentic-router` (and other repos that need to publish later)
6. **GitHub:** ogentic-router repo → Settings → Environments → "New environment" → `pypi`. No protection rules needed for v0.1.0; can add reviewers later for stability.

### 3c — Trigger the first release

```bash
git tag v0.1.0
git push origin v0.1.0
```

The release workflow fires. After it goes green, https://pypi.org/project/ogentic-router/ exists.

### 3d — Tighten the token + switch to trusted publisher

Once v0.1.0 is on PyPI:

1. Rotate the token from "all projects" → "Project: ogentic-router" only. Update the org secret value.
2. **Switch to trusted publisher (OIDC, no token rotation forever):**
   - PyPI project → "Publishing" → "Add a new publisher".
   - Owner: `OgenticAI`, Repository: `ogentic-router`, Workflow filename: `release.yml`, Environment: `pypi`.
   - In `release.yml`, swap `password: ${{ secrets.PYPI_API_TOKEN }}` for nothing (delete the `user`/`password` block), add `permissions: id-token: write` on the `publish` job.
3. Test on the next release (v0.1.1).

---

## §4 — Yanking a release

PyPI doesn't let you delete a version (would break downstream pins). What you can do:

1. **Yank** — the version is marked "do not install for new resolves" but stays installable for anyone who pinned it. Use this for releases that are broken but not malicious.
2. **Cut a fix** — bump and ship X.Y.(Z+1).

Project page → "Manage" → "Releases" → "Yank".

Never publish a brand-new project under the same name to "replace" a yanked release — PyPI doesn't allow re-uploading the same version even after a delete.

---

## §5 — Rollback / "we shipped the wrong thing"

If the workflow uploaded the wrong artifact (e.g., wrong commit was tagged):

1. **Don't try to overwrite.** PyPI forbids re-uploads of the same version.
2. Yank the bad release (§4).
3. Cut a patched X.Y.(Z+1) with the right content.
4. Update the changelog noting the yanked version.

---

## §6 — Common pitfalls

- **Forgot to bump `__init__.py`** — workflow's smoke step catches this. Cut `(Z+1)` with the bump.
- **Tag exists but workflow didn't fire** — check the tag matches `v*` (lowercase v, no extra prefix).
- **`twine check` fails on README** — usually a relative image link or unsupported reStructuredText directive. Use absolute https:// URLs for images.
- **Wheel contains `tests/`** — `[tool.hatch.build.targets.wheel] packages = ["src/ogentic_router"]` should be set; verify.
- **`pip install` returns 404 right after publish** — PyPI's CDN takes 30-60s to propagate. The workflow's smoke step retries 5×; if it still fails, it's worth checking the PyPI status page (https://status.python.org/).

---

## §7 — Cross-project releases

This file's pattern (release workflow + RELEASING.md) mirrors `ogentic-shield` (OGE-588, shipped v0.3.0 to PyPI). Both packages now follow the same tag-triggered release flow. The `ogentic-audit` package should copy this pattern when it hits v0.1.

Once all three OSS projects are on PyPI, set up org-level dependency-update automation between them (Dependabot for Python is enabled by default on GitHub Actions; same actions watch our pinned `ogentic-shield` deps in router + audit).

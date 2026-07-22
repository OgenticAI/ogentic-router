# Policy DSL reference

A policy is a YAML file. The router loads it with `Policy.from_yaml(path)` and
evaluates it against a Shield classification to produce a `RouteDecision`.
Rules are **first-match-wins**, evaluated top to bottom; if no rule matches, the
`default_backend` fires.

The schema is strict — unknown keys are rejected (`extra="forbid"`), so a typo
fails loudly at load time rather than silently widening a rule.

## Top-level fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `version` | integer | yes | Must be exactly `1`. |
| `default_backend` | string | yes | Non-empty. Fires when no rule matches. |
| `rules` | list of rules | no | Defaults to `[]` (every prompt → `default_backend`). |
| `budget` | mapping | no | Per-call cost ceiling. **Enforcement is ON by default** — see [Budget](#budget). |

## Budget

Cost enforcement is **on by default**: a policy with no `budget:` block still
enforces a per-call estimated-cost ceiling of **$1.00**. The router estimates the
input cost of a prompt *before* it leaves the device and raises
`BudgetCeilingExceeded` if the estimate exceeds the ceiling — no partial send.

| Field | Type | Default | Notes |
|---|---|---|---|
| `enforce` | bool | `true` | Set `false` to **opt this engagement out** entirely. |
| `ceiling_usd` | number > 0 | `1.00` | Per-call estimated-USD cap. |

```yaml
budget:
  enforce: true       # ON by default; false opts this deployment out
  ceiling_usd: 0.50   # tune per engagement
```

The default ceiling is deliberately generous — a single normal prompt estimates
at fractions of a cent, so the default never bites real usage; it catches
fat-finger / runaway mega-prompts and misconfigured batch jobs. Tune it down per
engagement for tighter control.

Precedence at call time (`Router.route`): an explicit `budget_ceiling=` argument
wins for that call (a number overrides the policy ceiling; `None` disables
enforcement for that one call); otherwise the policy's `budget` applies.

## Rule fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Non-empty. Appears in `RouteDecision.rule_id` for audit. |
| `when` | mapping | yes | The match condition. See predicates below. |
| `route` | string | yes | Non-empty. The backend id to route to on a match. |
| `transform` | string | no | Currently only `shield_redact`. Default: none. |

## `when` predicates

Every predicate you set must be satisfied for the rule to match (**AND** across
the keys you include). Omit a predicate to leave it unconstrained. A `when:`
with no predicates is always-true — an explicit catch-all you can place before
`default_backend`.

| Predicate | Type | Meaning |
|---|---|---|
| `groups_include` | list of strings | Matches if the classification's category groups intersect this list. |
| `groups_exclude` | list of strings | Matches only if the classification's groups do **not** intersect this list. |
| `sensitivity_score_gte` | int, 0–100 | Matches if `score >= N` (inclusive). |
| `sensitivity_score_lt` | int, 1–101 | Matches if `score < N` (exclusive). |
| `category_in` | list of strings | Matches if **any** detected entity's category is in this list. |
| `category_not_in` | list of strings | Matches if **no** detected entity's category is in this list. |

`groups_include` / `groups_exclude` values are validated at load time against
`ogentic_shield.CategoryGroup` (e.g. `PRIVILEGE`, `PHI`, `MNPI`). An unknown
group name fails the load with a `Did you mean …?` suggestion. (This validation
requires the `[shield]` extra to be installed.)

Score bounds are enforced by the schema: `sensitivity_score_gte` accepts 0–100,
`sensitivity_score_lt` accepts 1–101, so `sensitivity_score_lt: 101` is the way
to express "any score".

## `transform`

| Value | Effect |
|---|---|
| `shield_redact` | Marks the decision so the dispatcher applies `Shield.redact()` before sending to the chosen (cloud) backend. |

`transform` is carried on the `RouteDecision` as an enum; the value is unwrapped
to its string (`"shield_redact"`) by `RouteDecision.to_dict()`.

## What evaluation returns

`Policy.evaluate(classification)` (and `Router.route(...)`) returns a frozen
`RouteDecision`:

| Field | Type | Meaning |
|---|---|---|
| `backend_id` | string | The backend to route to (a rule's `route`, or `default_backend`). |
| `rule_id` | string \| None | The `id` of the matching rule; `None` when `default_backend` fired. |
| `transform` | Transform \| None | `shield_redact`, or `None`. |
| `reasoning` | string | Human-readable explanation, e.g. `"no rule matched; default_backend fired"`. |

`RouteDecision.to_dict()` gives you a plain dict suitable for JSON-lines audit
logging (see `examples/audit_replay.py`).

## Errors

Policy problems raise a single exception type, `PolicyError` (a `ValueError`).
It is raised for:

- An unreadable policy file — `Cannot read policy file '<path>': <reason>`.
- Invalid YAML — `Invalid YAML in '<path>': <reason>`.
- A top-level value that isn't a mapping — `Policy file '<path>' must contain a YAML mapping at the top level, got <type>`.
- Schema validation failures — a multi-line `Policy validation failed:` message
  with one `- <json-path>: <message>` line per problem (unknown key, wrong type,
  out-of-range score, unknown group name, etc.).

## Worked example

The canonical policy shipped in [`examples/policy.yaml`](../examples/policy.yaml):

```yaml
version: 1
default_backend: ollama-local

rules:
  - id: "privilege-stays-local"
    when:
      groups_include: [PRIVILEGE, PHI, MNPI]
    route: ollama-local

  - id: "high-sensitivity-stays-local"
    when:
      sensitivity_score_gte: 70
    route: ollama-local

  - id: "medium-redact-then-cloud"
    when:
      sensitivity_score_gte: 30
    route: openai-cloud
    transform: shield_redact

  - id: "low-cloud"
    when:
      sensitivity_score_gte: 0
    route: openai-cloud
```

Read top-to-bottom: privileged/PHI/MNPI content stays local; anything scoring
≥ 70 stays local; 30–69 goes to cloud **after** redaction; everything else goes
to cloud in the clear. Because the last rule matches any score ≥ 0, the
`default_backend` only fires if `rules` is empty.

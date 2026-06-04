"""Public exception type for policy-loading and policy-validation failures.

Single exception class on purpose — external users (Sotto Desktop, third-party
integrators, the OGE-585 CLI's ``policies validate``) should only need to
``except PolicyError`` to handle every failure mode of ``Policy.from_yaml`` /
``Policy.from_dict``. The error message is part of the public API surface —
wording matters because it will be Google-searched.
"""

from __future__ import annotations


class PolicyError(ValueError):
    """Raised when a policy file fails to load or validate.

    Subclasses :class:`ValueError` so generic ``except (ValueError, ...)``
    handlers still catch it, but the dedicated type makes ``except
    PolicyError`` the recommended pattern.

    The exception's ``str()`` carries everything a human operator needs:
    the file path (when known), a one-line summary, and the JSON-path of
    the offending field for schema errors (``rules → 0 → when →
    groups_include``).
    """

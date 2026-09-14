"""Auth dependency for the /me routes.

Consolidated onto services.common.auth.get_current_user — the single authentication
chokepoint. The local copy that lived here validated the token but did NOT put the caller's
tenant into context, so routes depending on it ran untenanted (and, once tenant enforcement is
enabled, would see nothing). Re-exported so existing imports keep working.

The common dependency returns a SUPERSET of the raw JWT payload (it still exposes "sub"),
so consumers of this module are unaffected.
"""

from services.common.auth import get_current_user  # noqa: F401

__all__ = ["get_current_user"]

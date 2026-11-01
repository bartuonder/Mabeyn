from apps.api.deps import TenantDb


def test_tenant_sessions_commit_before_the_response_is_sent():
    """A client that reads right after a write must see it, so writes commit first.

    The test client waits for the whole response, so the race cannot be reproduced there;
    instead the shared dependency must declare the early-exit scope.
    """
    (depends,) = TenantDb.__metadata__
    assert depends.scope == "function"

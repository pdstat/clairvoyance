class EndpointError(Exception):
    pass


class AuthError(Exception):
    """Raised when too many consecutive 401/403 responses are received."""
    pass

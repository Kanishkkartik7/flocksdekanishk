"""Error types. Every error the API returns has the same JSON shape: {"error": {"code", "message", "details"}}."""


class ApiError(Exception):
    """Base class for errors that map directly to an HTTP response."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(ApiError):
    status_code = 404
    code = "not_found"


class InvalidRequestError(ApiError):
    status_code = 422
    code = "invalid_request"


class PortalError(ApiError):
    """The upstream portal failed in a way the client could not recover from."""

    status_code = 502
    code = "portal_error"


class PortalAuthError(PortalError):
    code = "portal_auth_failed"


class PortalUnavailableError(PortalError):
    code = "portal_unavailable"


class PortalTimeoutError(PortalError):
    status_code = 504
    code = "portal_timeout"


class PortalProtocolError(PortalError):
    """The portal answered, but not in the shape we expect (it may have changed)."""

    code = "portal_unexpected_response"


class PortalNotFound(Exception):
    """Internal signal: the portal returned 404 for a resource. The service layer turns it into NotFoundError."""

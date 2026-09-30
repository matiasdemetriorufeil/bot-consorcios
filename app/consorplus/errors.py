class ConsorPlusError(Exception):
    """Base error for everything that goes wrong talking to ConsorPlus."""


class ForbiddenActionError(ConsorPlusError):
    """A request outside the read-only allowlist was attempted. It was NOT sent."""


class LoginError(ConsorPlusError):
    """Login failed (wrong credentials or an unexpected login flow)."""


class SessionExpiredError(ConsorPlusError):
    """The server redirected to login.aspx: the session is no longer valid."""


class ConsorPlusUnavailableError(ConsorPlusError):
    """The server did not answer after all the retries."""


class NotFoundError(ConsorPlusError):
    """The requested building or unit is not in the combo."""


class ParseError(ConsorPlusError, ValueError):
    """The HTML or delta response does not have the expected structure."""


class UnexpectedRedirectError(ForbiddenActionError):
    """ConsorPlus redirected somewhere outside the allowlist. The redirect was NOT followed."""

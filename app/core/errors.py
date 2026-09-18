class AppError(Exception):
    """Base class for application-level errors.

    M7 grows this into the full hierarchy behind the exception handlers; it
    starts here because M2's `decode_token` already needs a member of it.
    """


class CredentialsError(AppError):
    """A token could not be validated.

    Carries no detail on purpose: expired, malformed, wrongly signed and
    signed-with-the-wrong-algorithm all collapse into this one error so
    callers cannot learn which check failed.
    """

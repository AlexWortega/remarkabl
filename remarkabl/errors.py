class PermanentError(Exception):
    """Retrying the same input cannot help."""


class NotConfigured(Exception):
    """Keep work queued until configuration is supplied."""


class RetryableError(Exception):
    """Temporary external failure, with no credentials in the message."""

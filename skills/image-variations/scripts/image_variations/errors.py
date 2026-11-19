class VariationError(Exception):
    """A safe, structured error; never store raw HTTP/provider exceptions here."""

    def __init__(self, code, message, *, details=None, retryable=False, retry_after=0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.retryable = retryable
        self.retry_after = min(max(float(retry_after), 0), 60)

    def as_dict(self):
        return {"code": self.code, "message": self.message, "details": self.details}


def require(condition, code, message, **details):
    if not condition:
        raise VariationError(code, message, details=details)

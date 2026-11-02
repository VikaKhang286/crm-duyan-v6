"""Small helpers for keeping Pancake credentials out of logs and responses."""
import re


_SENSITIVE_VALUE = re.compile(
    r'''(?ix)(["']?\b(?:access_token|page_access_token|api_key)\b["']?\s*[=:]\s*["']?)([^&\s,;"']+)'''
)
_BEARER_VALUE = re.compile(r"(?i)(\bBearer\s+)([^\s,;]+)")


def redact_pancake_secrets(value):
    """Return display-safe text without query/header credential values."""
    text = str(value or '')
    text = _SENSITIVE_VALUE.sub(r"\1***REDACTED***", text)
    return _BEARER_VALUE.sub(r"\1***REDACTED***", text)

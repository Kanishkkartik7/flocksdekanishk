"""HMAC request signing, as the portal's own "Export all meters" button does it.

The portal's JavaScript builds the text  METHOD \\n PATH \\n QUERY \\n UNIX_SECONDS, signs it with HMAC-SHA256
using a secret from GET /portal/keys, and sends the hex digest in `x-signature` plus the time in `x-timestamp`.
"""

import hashlib
import hmac


def build_signing_message(method: str, path: str, query: str, timestamp: int) -> str:
    return "\n".join([method, path, query, str(timestamp)])


def sign_request(secret: str, method: str, path: str, query: str, timestamp: int) -> str:
    message = build_signing_message(method, path, query, timestamp)
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()

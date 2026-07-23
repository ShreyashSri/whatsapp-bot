import hashlib
import hmac


def verify_openwa_signature(body: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not secret:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)

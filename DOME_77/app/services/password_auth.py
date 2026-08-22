import hashlib
import hmac
import os


def _verification_payload(email: str, code: str, purpose: str) -> str:
    return f"{purpose}|{email.strip().lower()}|{code}"


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    iterations = 600_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, expected_hex = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations),
        )
        return hmac.compare_digest(actual.hex(), expected_hex)
    except Exception:
        return False


def hash_verification_code(email: str, code: str, purpose: str = "verify") -> str:
    """Store short-lived numeric codes with a random salt and a slow hash."""
    return hash_password(_verification_payload(email, code, purpose))


def verify_verification_code(
    email: str,
    code: str,
    stored: str,
    purpose: str = "verify",
) -> bool:
    return verify_password(_verification_payload(email, code, purpose), stored)

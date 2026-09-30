"""Password hashing, session helpers and CSRF protection."""
import secrets

import bcrypt

SESSION_USER_KEY = "user_id"
SESSION_CSRF_KEY = "csrf_token"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def get_csrf_token(session: dict) -> str:
    token = session.get(SESSION_CSRF_KEY)
    if not token:
        token = new_csrf_token()
        session[SESSION_CSRF_KEY] = token
    return token


def validate_csrf(session: dict, submitted: str | None) -> bool:
    expected = session.get(SESSION_CSRF_KEY, "")
    if not expected or not submitted:
        return False
    return secrets.compare_digest(str(expected), str(submitted))

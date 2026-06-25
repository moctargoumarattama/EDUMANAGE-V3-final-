import os
from pathlib import Path

from .i18n import DEFAULT_LANGUAGE_CODE, get_supported_languages


BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
INSTANCE_DIR = PROJECT_DIR / "instance"
MEDIA_DIR = PROJECT_DIR / "media"
STATIC_UPLOADS_DIR = BASE_DIR / "static" / "uploads"


def _default_sqlite_uri():
    return f"sqlite:///{(INSTANCE_DIR / 'ecole.db').as_posix()}"


def _env_flag(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-local-secret-key")
    SECURITY_PASSWORD_SALT = os.environ.get(
        "SECURITY_PASSWORD_SALT",
        "dev-only-local-password-salt",
    )

    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or os.environ.get(
        "SQLALCHEMY_DATABASE_URI",
        _default_sqlite_uri(),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    LANGUAGES = get_supported_languages()
    BABEL_DEFAULT_LOCALE = DEFAULT_LANGUAGE_CODE
    BABEL_DEFAULT_TIMEZONE = "UTC"

    INSTANCE_DIR = str(INSTANCE_DIR)
    MEDIA_ROOT = os.environ.get("MEDIA_ROOT", str(MEDIA_DIR))
    MEDIA_URL = (os.environ.get("MEDIA_URL", "/media") or "/media").rstrip("/")
    MEDIA_UPLOADS_ROOT = os.path.join(MEDIA_ROOT, "uploads")
    LEGACY_STATIC_UPLOADS_ROOT = str(STATIC_UPLOADS_DIR)

    UPLOAD_FOLDER = os.environ.get(
        "UPLOAD_FOLDER",
        os.path.join(MEDIA_UPLOADS_ROOT, "pdfs"),
    )
    COURSE_IMAGE_FOLDER = os.environ.get(
        "COURSE_IMAGE_FOLDER",
        os.path.join(MEDIA_UPLOADS_ROOT, "courses"),
    )
    LESSON_IMAGE_FOLDER = os.environ.get(
        "LESSON_IMAGE_FOLDER",
        os.path.join(MEDIA_UPLOADS_ROOT, "lecons"),
    )
    AUDIO_FOLDER = os.environ.get(
        "AUDIO_FOLDER",
        os.path.join(MEDIA_UPLOADS_ROOT, "audios"),
    )
    PROFILE_IMAGE_FOLDER = os.environ.get(
        "PROFILE_IMAGE_FOLDER",
        os.path.join(MEDIA_UPLOADS_ROOT, "profiles"),
    )
    PIPER_BINARY = os.environ.get("PIPER_BINARY", "")
    PIPER_MODEL = os.environ.get("PIPER_MODEL", "")
    PIPER_CONFIG = os.environ.get("PIPER_CONFIG", "")
    PIPER_SPEAKER = os.environ.get("PIPER_SPEAKER", "")
    PIPER_CACHE_FOLDER = os.environ.get(
        "PIPER_CACHE_FOLDER",
        os.path.join(AUDIO_FOLDER, "generated"),
    )
    ONLINE_TRANSLATION_ENABLED = os.environ.get("ONLINE_TRANSLATION_ENABLED", "1")
    TRANSLATION_HTTP_TIMEOUT = os.environ.get("TRANSLATION_HTTP_TIMEOUT", "4")
    LIBRETRANSLATE_URL = os.environ.get("LIBRETRANSLATE_URL", "")
    LIBRETRANSLATE_API_KEY = os.environ.get("LIBRETRANSLATE_API_KEY", "")
    MYMEMORY_TRANSLATE_URL = os.environ.get("MYMEMORY_TRANSLATE_URL", "")
    MYMEMORY_CONTACT_EMAIL = os.environ.get("MYMEMORY_CONTACT_EMAIL", "")
    BACKUP_FOLDER = os.environ.get(
        "BACKUP_FOLDER",
        os.path.join(INSTANCE_DIR, "backups"),
    )
    CACHE_DIR = os.environ.get(
        "CACHE_DIR",
        os.path.join(INSTANCE_DIR, "cache"),
    )
    LOG_FILE = os.environ.get(
        "LOG_FILE",
        os.path.join(PROJECT_DIR, "logs", "platform.log"),
    )
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024

    VAPID_PRIVATE_KEY = os.environ.get(
        "VAPID_PRIVATE_KEY",
        os.path.join(PROJECT_DIR, "vapid_private.pem"),
    )
    VAPID_PUBLIC_KEY = os.environ.get(
        "VAPID_PUBLIC_KEY",
        "BCUBDjoV-DO3l9tac9nURSQdf5ZQAmqoDcGqskqTpq87sBefL3fwb8mx6iyFQ9N6dob4dMY_teTEG2Muvqz0SHI",
    )
    VAPID_CLAIMS = {
        "sub": os.environ.get("VAPID_SUBJECT", "mailto:admin@ta-plateforme.com"),
    }

    GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    GOOGLE_OAUTH_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
    SESSION_COOKIE_SECURE = _env_flag("SESSION_COOKIE_SECURE", False)
    PREFERRED_URL_SCHEME = os.environ.get("PREFERRED_URL_SCHEME", "https")
    BACKUP_RETENTION_COUNT = int(os.environ.get("BACKUP_RETENTION_COUNT", "10"))
    BACKUP_ALERT_HOURS = int(os.environ.get("BACKUP_ALERT_HOURS", "48"))
    ERROR_SPIKE_THRESHOLD = int(os.environ.get("ERROR_SPIKE_THRESHOLD", "5"))

    VERSION = "2.4.0"
    ASSET_VERSION = os.environ.get("ASSET_VERSION", "20260617-quiz-memory-v1")

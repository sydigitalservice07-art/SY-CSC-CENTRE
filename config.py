"""
SY CSC CENTRE - configuration module
====================================
Reads settings from a local .env file (or real environment variables).
No third-party dependency is required to load the .env file.

Target: Python 3.8 (Windows 7/8/10), MySQL 5.7 / 8.0 / MariaDB 10.x
"""
import os
from decimal import Decimal

BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _load_dotenv(path):
    """Very small .env loader (KEY=VALUE per line, # comments)."""
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            # real environment variables always win
            os.environ.setdefault(key, value)


_load_dotenv(os.path.join(BASE_DIR, ".env"))


def _env(key, default=""):
    return os.environ.get(key, default)


def _env_int(key, default):
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_bool(key, default=False):
    value = str(os.environ.get(key, "1" if default else "0")).strip().lower()
    return value in ("1", "true", "yes", "on")


class Config(object):
    # ------------------------------------------------------------------ app
    APP_NAME = "SY CSC CENTRE"
    SECRET_KEY = _env("SECRET_KEY", "change-this-secret-key-in-dot-env-file")
    DEBUG = _env_bool("DEBUG", False)

    # ------------------------------------------------------------- database
    # DB_ENGINE = "mysql"  -> production (PyMySQL).  "sqlite" -> tests / demo.
    DB_ENGINE = _env("DB_ENGINE", "mysql").lower()
    DB_HOST = _env("DB_HOST", "127.0.0.1")
    DB_PORT = _env_int("DB_PORT", 3306)
    DB_USER = _env("DB_USER", "sycsc")
    DB_PASSWORD = _env("DB_PASSWORD", "sycsc")
    DB_NAME = _env("DB_NAME", "sycsc_centre")
    DB_CHARSET = "utf8mb4"
    SQLITE_PATH = _env("SQLITE_PATH", os.path.join(BASE_DIR, "instance", "sycsc.db"))

    # ---------------------------------------------------------------- files
    UPLOAD_FOLDER = _env("UPLOAD_FOLDER", os.path.join(BASE_DIR, "uploads"))
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024          # hard request cap: 8 MB
    PHOTO_MAX_BYTES = _env_int("PHOTO_MAX_BYTES", 50 * 1024)      # 50 KB target
    SIGNATURE_MAX_BYTES = _env_int("SIGNATURE_MAX_BYTES", 50 * 1024)
    DOCUMENT_MAX_BYTES = _env_int("DOCUMENT_MAX_BYTES", 2 * 1024 * 1024)
    IMAGE_MAX_PIXELS = 1600
    ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "webp", "bmp"}
    ALLOWED_DOC_EXT = {"pdf", "png", "jpg", "jpeg", "webp", "txt", "doc", "docx", "xls", "xlsx"}

    # ------------------------------------------------------------- sessions
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12      # 12 hours
    REMEMBER_COOKIE_DURATION = 60 * 60 * 24 * 7

    # ---------------------------------------------------------------- misc
    DEFAULT_LANG = _env("DEFAULT_LANG", "gu")      # gu = Gujarati, en = English
    ITEMS_PER_PAGE = _env_int("ITEMS_PER_PAGE", 20)
    CURRENCY = "\u20b9"                            # Rupee sign
    # How the PDF "Charge" column is classified in accounting.
    # Allowed: csc_income | govt_charge | other_income
    CHARGE_CLASSIFICATION = _env("CHARGE_CLASSIFICATION", "csc_income")
    HOST = _env("HOST", "0.0.0.0")
    PORT = _env_int("PORT", 5000)

    @staticmethod
    def money_zero():
        return Decimal("0.00")


def db_config():
    """Return a plain dict used by modules.db.Database."""
    return {
        "engine": Config.DB_ENGINE,
        "host": Config.DB_HOST,
        "port": Config.DB_PORT,
        "user": Config.DB_USER,
        "password": Config.DB_PASSWORD,
        "database": Config.DB_NAME,
        "charset": Config.DB_CHARSET,
        "sqlite_path": Config.SQLITE_PATH,
    }

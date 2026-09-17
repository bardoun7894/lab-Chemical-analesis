"""
Flask Application Configuration
"""

import os

basedir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


class Config:
    """Base configuration"""

    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-secret-key-change-in-production"

    # Database - Use existing SQLite database
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get("DATABASE_URL")
        or f"sqlite:///{os.path.join(basedir, 'lab_chemical.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Connection pooling, Postgres only.
    #
    # A SQLite "connection" is a file handle and cannot go stale. A Postgres one
    # can: restart the database, or let a firewall drop an idle TCP session, and
    # the pool keeps handing out sockets that are already dead. Each one throws
    # on first use, so the symptom is a burst of 500s that clears up on its own
    # and is nearly impossible to reproduce afterwards.
    #
    # pool_pre_ping issues a cheap SELECT 1 before lending a connection out and
    # transparently reconnects if it fails. pool_recycle retires connections
    # before they reach the age where something upstream would kill them.
    #
    # Sizing: 4 gunicorn workers, each with its own pool. 5 + 10 overflow means
    # a worst case of 60 connections against a max_connections of 100, which
    # leaves room for psql, backups and the dev instance on the same server.
    _pool_options = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_size": 5,
        "max_overflow": 10,
    }
    # SQLite uses a pool class that rejects pool_size/max_overflow, so only the
    # engine that needs these options gets them.
    SQLALCHEMY_ENGINE_OPTIONS = (
        _pool_options
        if SQLALCHEMY_DATABASE_URI.startswith(("postgresql://", "postgresql+"))
        else {}
    )

    # Babel (i18n)
    LANGUAGES = ["en", "ar"]
    BABEL_DEFAULT_LOCALE = "en"
    BABEL_DEFAULT_TIMEZONE = "UTC"

    # Upload settings
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB max file upload
    UPLOAD_FOLDER = os.path.join(basedir, "uploads")

    # Session
    SESSION_TYPE = "filesystem"
    PERMANENT_SESSION_LIFETIME = 3600 * 8  # 8 hours

    # Email Configuration (for daily reports)
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", 587))
    MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@gcpipes.com")

    # Daily Report Recipients
    DAILY_REPORT_RECIPIENTS = os.environ.get("DAILY_REPORT_RECIPIENTS", "").split(",")


class DevelopmentConfig(Config):
    """Development configuration"""

    DEBUG = True


class ProductionConfig(Config):
    """Production configuration"""

    DEBUG = False
    # In production, set SECRET_KEY via environment variable


class TestingConfig(Config):
    """Testing configuration"""

    TESTING = True
    # In-memory SQLite by default, because it keeps the local suite fast.
    #
    # But production runs Postgres, and the two disagree in ways that decide
    # whether a test means anything: SQLite ignores foreign keys entirely,
    # its LIKE is case-insensitive while Postgres' is not, and it will accept
    # values Postgres rejects outright. A green suite on SQLite is therefore
    # not evidence about the system actually deployed.
    #
    # CI sets TEST_DATABASE_URL to a real Postgres so the suite runs against
    # the engine that serves users. Point it at one locally too when a change
    # touches queries, constraints or migrations.
    SQLALCHEMY_DATABASE_URI = (
        os.environ.get("TEST_DATABASE_URL") or "sqlite:///:memory:"
    )

    # Recomputed from the URI above, not inherited. The parent's value is
    # derived from DATABASE_URL, so a developer with Postgres in their
    # environment would otherwise hand pool_size to an in-memory SQLite engine,
    # whose pool class rejects it and fails every test at setup.
    SQLALCHEMY_ENGINE_OPTIONS = (
        Config._pool_options
        if SQLALCHEMY_DATABASE_URI.startswith(("postgresql://", "postgresql+"))
        else {}
    )


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}

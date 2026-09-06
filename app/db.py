from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def _connect_args(database_url: str) -> dict:
    return {"check_same_thread": False} if database_url.startswith("sqlite") else {}


def build_engine(database_url: str | None = None):
    settings = get_settings()
    url = database_url or settings.database_url
    kwargs = {
        "pool_pre_ping": True,
        "connect_args": _connect_args(url),
    }
    # SQLite uses a different pool implementation in tests/development. For
    # PostgreSQL keep both connection count and wait time bounded: under a traffic
    # spike a request should fail fast with a retryable 503 instead of occupying a
    # worker thread for SQLAlchemy's long default pool wait.
    if not url.startswith("sqlite"):
        kwargs.update(
            pool_size=max(1, int(settings.database_pool_size)),
            max_overflow=max(0, int(settings.database_max_overflow)),
            pool_timeout=max(0.5, float(settings.database_pool_timeout_seconds)),
            pool_recycle=max(60, int(settings.database_pool_recycle_seconds)),
        )
    return create_engine(url, **kwargs)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    import app.models  # noqa:F401
    import app.models_sprint27  # noqa:F401
    import app.models_sprint29  # noqa:F401
    import app.models_sprint31  # noqa:F401
    import app.models_sprint37  # noqa:F401
    import app.models_sprint38  # noqa:F401
    try:
        import app.models_sprint30  # noqa:F401
    except ModuleNotFoundError:
        pass
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

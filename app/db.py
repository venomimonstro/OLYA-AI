from collections.abc import Generator
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def _env_int(name: str, default: int, *, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return max(minimum, int(default))


def _connect_args(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    if database_url.startswith(("postgresql", "postgres")):
        connect_timeout = _env_int("X1_DATABASE_CONNECT_TIMEOUT_SECONDS", 5, minimum=1)
        statement_timeout = _env_int("X1_DATABASE_STATEMENT_TIMEOUT_MS", 30_000, minimum=1_000)
        lock_timeout = _env_int("X1_DATABASE_LOCK_TIMEOUT_MS", 10_000, minimum=250)
        idle_tx_timeout = _env_int("X1_DATABASE_IDLE_TRANSACTION_TIMEOUT_MS", 60_000, minimum=1_000)
        return {
            "connect_timeout": connect_timeout,
            "options": (
                f"-c statement_timeout={statement_timeout} "
                f"-c lock_timeout={lock_timeout} "
                f"-c idle_in_transaction_session_timeout={idle_tx_timeout}"
            ),
        }
    return {}


def build_engine(database_url: str | None = None):
    settings = get_settings()
    url = database_url or settings.database_url
    kwargs = {"pool_pre_ping": True, "connect_args": _connect_args(url)}
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
    import app.models_sprint45  # noqa:F401
    import app.models_sprint51  # noqa:F401
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

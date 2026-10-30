import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Load environment variables
load_dotenv()


def normalize_database_url(url: str) -> str:
    """Make PostgreSQL URLs work with the psycopg 3 driver we install.

    "postgres://..." and "postgresql://..." (common from hosting providers) make
    SQLAlchemy look for the old psycopg2 driver, which is not installed.
    """
    url = url.strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = normalize_database_url(os.getenv("DATABASE_URL", ""))

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not configured. Example: "
        "postgresql+psycopg://user:password@localhost:5432/aether"
    )

_engine_options = {"pool_pre_ping": True}
if not DATABASE_URL.startswith("sqlite"):
    # Connection-pool settings only apply to server databases such as PostgreSQL
    _engine_options.update(
        pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
        pool_recycle=1800,
    )

# Create database engine
engine = create_engine(DATABASE_URL, **_engine_options)

# Create database sessions
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


# Base class for database models
class Base(DeclarativeBase):
    pass


# Provide database session to FastAPI routes
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Initialize database tables (development only; use Alembic migrations in production)
def init_db():
    # Register the customer table
    from app.models.user import User  # noqa: F401

    # Register the revoked JWT table
    from app.models.revoked_token import RevokedToken  # noqa: F401

    from app.models.usage import MonthlyUsage, AIUsageEvent  # noqa: F401

    # Create tables that do not already exist
    Base.metadata.create_all(bind=engine)
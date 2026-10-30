"""Read the existing DATABASE_URL from the application environment."""
import os
from logging.config import fileConfig
from pathlib import Path
from dotenv import load_dotenv
from alembic import context
from sqlalchemy import engine_from_config, pool

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
from app.database import Base, normalize_database_url
from app.models.user import User  # noqa: F401
from app.models.revoked_token import RevokedToken  # noqa: F401
from app.models.usage import MonthlyUsage, AIUsageEvent  # noqa: F401

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
config.set_main_option("sqlalchemy.url", normalize_database_url(os.environ["DATABASE_URL"]).replace("%", "%%"))
target_metadata = Base.metadata

def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

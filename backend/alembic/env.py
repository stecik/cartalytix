from sqlalchemy import create_engine, pool

from alembic import context
from cartalytix.models import Base
from cartalytix.settings import Settings

config = context.config
target_metadata = Base.metadata
url = config.get_main_option("sqlalchemy.url") or Settings().database_url
if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()

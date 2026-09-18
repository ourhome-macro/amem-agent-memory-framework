from alembic import context
from sqlalchemy import URL, create_engine

engine = create_engine(URL.create("sqlite", database=context.config.attributes["db_path"]))
with engine.connect() as connection:
    context.configure(connection=connection, render_as_batch=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()

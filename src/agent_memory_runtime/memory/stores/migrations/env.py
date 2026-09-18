from alembic import context
from sqlalchemy import URL, create_engine

manager = context.config.attributes["manager"]
engine = create_engine(URL.create("sqlite", database=str(manager.path)))
with engine.connect() as connection:
    context.configure(
        connection=connection, version_table="amem_alembic_version", render_as_batch=True
    )
    with context.begin_transaction():
        context.run_migrations()
engine.dispose()

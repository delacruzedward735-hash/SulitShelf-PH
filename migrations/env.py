import logging
from logging.config import fileConfig

from alembic import context
from flask import current_app

config = context.config
fileConfig(config.config_file_name)
logger = logging.getLogger("alembic.env")
config.set_main_option("sqlalchemy.url", str(current_app.extensions["migrate"].db.engine.url).replace("%", "%%"))
target_db = current_app.extensions["migrate"].db


def get_metadata():
    return target_db.metadata


def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=get_metadata(), literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = target_db.engine
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=get_metadata(), compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()

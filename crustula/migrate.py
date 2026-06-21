"""Standalone, idempotent DB migration for Crustula.

Adds the ``account`` table and the new ``jar.headers`` / ``jar.account_id`` columns
to a pre-existing ``crustula.db``. SQLModel's ``create_all`` creates missing tables
but never ALTERs existing ones, so the new columns are added explicitly here.

Usage::

    python -m crustula.migrate                # migrates ./crustula.db
    python -m crustula.migrate path/to.db     # migrates a specific file
    python -m crustula.migrate sqlite:///x.db # or an explicit SQLAlchemy URL

Safe to run repeatedly.
"""

import sys

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, create_engine

from . import main  # noqa: F401  (import registers the table models on SQLModel.metadata)


def migrate(engine: Engine) -> list[str]:
    """Bring the schema at ``engine`` up to date. Returns a list of applied changes."""
    applied: list[str] = []

    pre_tables = set(inspect(engine).get_table_names())

    # creates any missing tables (notably ``account``) without touching existing ones
    SQLModel.metadata.create_all(engine)
    applied.extend(f"created table {name}"
                   for name in sorted(set(inspect(engine).get_table_names()) - pre_tables))

    # add columns the models declare but that are missing from already-existing tables,
    # deriving each column's type from the model rather than a hardcoded list
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if table.name in pre_tables:
                existing = {col["name"] for col in inspector.get_columns(table.name)}
                for col in table.columns:
                    if col.name not in existing:
                        coltype = col.type.compile(engine.dialect)
                        conn.execute(text(
                            f"ALTER TABLE {table.name} ADD COLUMN {col.name} {coltype}"))
                        applied.append(f"added column {table.name}.{col.name}")

    return applied


def _engine_for(target: str) -> Engine:
    url = target if "://" in target else f"sqlite:///{target}"
    return create_engine(url, connect_args=main.connect_args)


def cli() -> None:
    """CLI entrypoint: optional first arg is a DB path or SQLAlchemy URL."""
    target = sys.argv[1] if len(sys.argv) > 1 else main.SQLLITE_URL
    engine = _engine_for(target)
    applied = migrate(engine)
    if applied:
        for change in applied:
            print(change)
    else:
        print("already up to date")


if __name__ == "__main__":
    cli()


def _sqlite_schema_upgrade():
    """
    Local SQLite files from an older RecoverAI version are
    backed up and recreated (demo data reseeds).

    Postgres/Supabase uses supabase/migrations/*.sql instead.
    """
    import os
    import time
    from sqlalchemy import inspect

    url = get_settings().database_url

    if not url.startswith("sqlite:///") or url == "sqlite:///:memory:":
        return

    path = url.replace("sqlite:///", "", 1)

    if not os.path.exists(path):
        return

    insp = inspect(dbmod.engine)
    stale = False

    for t in Base.metadata.sorted_tables:
        if not insp.has_table(t.name):
            stale = True
            break

        have = {
            c["name"]
            for c in insp.get_columns(t.name)
        }

        if any(c.name not in have for c in t.columns):
            stale = True
            break

    if stale:
        dbmod.engine.dispose()

        backup = f"{path}.bak-{int(time.time())}"
        os.replace(path, backup)

        log.warning(
            "Database schema changed: old SQLite file moved to %s "
            "and a fresh database was created",
            backup,
        )

        return

    _sqlite_widen_provider_check()


def _sqlite_widen_provider_check():
    """
    Rebuilds the integration_credentials table so it accepts
    new AI providers (e.g. Gemini).

    Existing saved credentials are copied to the new table.
    """
    from .models import PROVIDER_KEYS

    table = "integration_credentials"

    with dbmod.engine.begin() as conn:
        sql = conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name=?",
            (table,),
        ).scalar()

        if not sql:
            return

        if all(f"'{p}'" in sql for p in PROVIDER_KEYS):
            return

        cols = ", ".join(
            c.name
            for c in Base.metadata.tables[table].columns
        )

        conn.exec_driver_sql(
            f"ALTER TABLE {table} RENAME TO _old_{table}"
        )

        old_idx = conn.exec_driver_sql(
            "SELECT name FROM sqlite_master "
            "WHERE type='index' AND tbl_name=? "
            "AND sql IS NOT NULL",
            (f"_old_{table}",),
        ).fetchall()

        for (idx,) in old_idx:
            conn.exec_driver_sql(
                f'DROP INDEX "{idx}"'
            )

        Base.metadata.tables[table].create(conn)

        conn.exec_driver_sql(
            f"INSERT INTO {table} ({cols}) "
            f"SELECT {cols} FROM _old_{table}"
        )

        conn.exec_driver_sql(
            f"DROP TABLE _old_{table}"
        )

        log.warning(
            "Upgraded %s to allow new AI providers "
            "(saved credentials kept)",
            table,
        )


def init_db():
    _sqlite_schema_upgrade()

    Base.metadata.create_all(
        bind=dbmod.engine
    )

    s = get_settings()

    if s.seed_demo_on_start:
        from .seed import ensure_demo

        with dbmod.SessionLocal() as db:
            ensure_demo(db)

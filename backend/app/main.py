import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select

from . import db as dbmod
from .config import get_settings
from .db import Base
from .models import Merchant
from .routers import auth, insights, integrations, merchant, payments, paypal, recovery

log = logging.getLogger("recoverai")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _sqlite_schema_upgrade():
    """Local SQLite files from an older RecoverAI version are backed up and recreated (demo data reseeds).
    Postgres/Supabase uses supabase/migrations/*.sql instead."""
    import os
    import time
    from sqlalchemy import inspect
    url = get_settings().database_url
    if not url.startswith("sqlite:///") or url in ("sqlite:///:memory:",):
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
        have = {c["name"] for c in insp.get_columns(t.name)}
        if any(c.name not in have for c in t.columns):
            stale = True
            break
    if stale:
        dbmod.engine.dispose()
        backup = f"{path}.bak-{int(time.time())}"
        os.replace(path, backup)
        log.warning("Database schema changed: old SQLite file moved to %s and a fresh database was created", backup)
        return
    _sqlite_widen_provider_check()


def _sqlite_widen_provider_check():
    """Newer versions add AI providers (e.g. Gemini). SQLite can't alter a CHECK constraint, so the small
    integration_credentials table is rebuilt in place — every saved credential, account and payment is kept."""
    from .models import PROVIDER_KEYS
    table = "integration_credentials"
    with dbmod.engine.begin() as conn:
        sql = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).scalar()
        if not sql or all(f"'{p}'" in sql for p in PROVIDER_KEYS):
            return
        cols = ", ".join(c.name for c in Base.metadata.tables[table].columns)
        conn.exec_driver_sql(f"ALTER TABLE {table} RENAME TO _old_{table}")
        for (idx,) in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                                           (f"_old_{table}",)).fetchall():
            conn.exec_driver_sql(f'DROP INDEX "{idx}"')
        Base.metadata.tables[table].create(conn)
        conn.exec_driver_sql(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _old_{table}")
        conn.exec_driver_sql(f"DROP TABLE _old_{table}")
    log.warning("Upgraded %s to allow new AI providers (saved credentials kept)", table)


def init_db():
    _sqlite_schema_upgrade()
    Base.metadata.create_all(bind=dbmod.engine)
    s = get_settings()
    if s.seed_demo_on_start:
        from .seed import ensure_demo
        with dbmod.SessionLocal() as db:
            ensure_demo(db)


async def agent_loop(seconds: int):
    from .services import agent
    while True:
        await asyncio.sleep(seconds)
        try:
            with dbmod.SessionLocal() as db:
                for mid in db.scalars(select(Merchant.id)).all():
                    agent.tick(db, mid)
                    db.commit()
        except Exception:  # never crash the server because of the loop
            log.exception("agent loop error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.app_env == "production" and s.jwt_secret.startswith("change-me"):
        raise RuntimeError("Set JWT_SECRET in production")
    if s.app_env != "test":
        init_db()
    task = asyncio.create_task(agent_loop(s.agent_loop_seconds)) if s.agent_loop_seconds > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="RecoverAI API", version="1.0.0", lifespan=lifespan,
              description="AI-powered payment recovery for PayPal merchants. All payment state is confirmed by PayPal APIs/webhooks.")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in get_settings().cors_origins.split(",") if o.strip()],
                   allow_credentials=False, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Authorization", "Content-Type"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    errs = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse({"detail": errs[0]["message"] if errs else "Invalid request", "errors": errs}, status_code=422)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Internal error. It has been logged."}, status_code=500)


for r in (auth.router, payments.router, recovery.router, paypal.router, insights.router, merchant.router, integrations.router, recovery.extra):
    app.include_router(r)


@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "paypal_configured": s.paypal_configured, "llm_configured": s.llm_configured, "email_configured": s.email_configured}

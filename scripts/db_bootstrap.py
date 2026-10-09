"""Create the local database and the two login roles the apps connect as.

    uv run --project backend python scripts/db_bootstrap.py create-db   --database insightfulcell
    uv run --project backend python scripts/db_bootstrap.py create-logins --database insightfulcell

Two stages because the NOLOGIN role `ic_app` is created by the first Alembic migration:
  1. create-db      database + `ic_auth` (the web app's role; owns only the `auth` schema)
  2. (migrate)      `alembic upgrade head` as the owner
  3. create-logins  `ic_app_dev` = LOGIN member of `ic_app` (what the API connects as)

Development only: passwords are fixed and well known, so it refuses non-local hosts.
"""

from __future__ import annotations

import argparse
import os
import sys

import psycopg
from psycopg import sql

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}
AUTH_ROLE, AUTH_PASSWORD = "ic_auth", "ic_auth_dev"
APP_ROLE, APP_PASSWORD = "ic_app_dev", "ic_app_dev"


def _connect(url: str, database: str | None = None) -> psycopg.Connection:
    conninfo = url.replace("postgresql+psycopg://", "postgresql://")
    kwargs = {"dbname": database} if database else {}
    return psycopg.connect(conninfo, autocommit=True, **kwargs)  # type: ignore[arg-type]


def _ensure_login(cur: psycopg.Cursor, role: str, password: str, member_of: str | None) -> None:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
    if cur.fetchone() is None:
        cur.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOBYPASSRLS PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
    if member_of:
        cur.execute(
            sql.SQL("GRANT {} TO {}").format(sql.Identifier(member_of), sql.Identifier(role))
        )


def create_db(url: str, database: str) -> None:
    with _connect(url) as admin, admin.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
        if cur.fetchone() is None:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        _ensure_login(cur, AUTH_ROLE, AUTH_PASSWORD, None)
    with _connect(url, database) as db, db.cursor() as cur:
        cur.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS auth AUTHORIZATION {}").format(
                sql.Identifier(AUTH_ROLE)
            )
        )
        # The web app's role can use its own schema and nothing else.
        cur.execute(
            sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(sql.Identifier(AUTH_ROLE))
        )
        cur.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(database), sql.Identifier(AUTH_ROLE)
            )
        )
    print(f"Database '{database}' and role '{AUTH_ROLE}' are ready.")


def create_logins(url: str, database: str) -> None:
    with _connect(url, database) as db, db.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'ic_app'")
        if cur.fetchone() is None:
            raise SystemExit("Role ic_app does not exist yet: run the migrations first.")
        _ensure_login(cur, APP_ROLE, APP_PASSWORD, "ic_app")
    print(f"Role '{APP_ROLE}' (member of ic_app) is ready.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("stage", choices=["create-db", "create-logins"])
    parser.add_argument("--database", required=True)
    parser.add_argument(
        "--admin-url",
        default=os.environ.get("TEST_DATABASE_ADMIN_URL")
        or "postgresql+psycopg://ic_owner:ic_owner@localhost:5432/postgres",
    )
    args = parser.parse_args()

    host = (args.admin_url.split("@", 1)[-1].split("/", 1)[0]).rsplit(":", 1)[0]
    if host not in LOCAL_HOSTS:
        print(
            f"Refusing to create fixed-password roles on '{host}' (development only).",
            file=sys.stderr,
        )
        return 2
    (create_db if args.stage == "create-db" else create_logins)(args.admin_url, args.database)
    return 0


if __name__ == "__main__":
    sys.exit(main())

# ruff: noqa: E501
"""Capture a consistent pg_dump and verify an actual isolated restoration.

Credentials are taken from environment variables and are never command arguments.
Run ``python -m app.backups --help``. Requires pg_dump on PATH.
"""

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.core.config import get_settings, normalize_database_url


def manifest(connection, tenant_id: UUID) -> dict:
    connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    tables = connection.execute(text("""SELECT t.table_name,
      EXISTS(SELECT 1 FROM information_schema.columns c
        WHERE c.table_schema='public' AND c.table_name=t.table_name AND c.column_name='tenant_id') scoped
      FROM information_schema.tables t WHERE t.table_schema='public'
        AND t.table_type='BASE TABLE' ORDER BY t.table_name""")).mappings()
    result = {}
    for definition in tables:
        table = definition["table_name"]
        # Names originate exclusively from PostgreSQL's catalog, not user/AI input.
        quoted = connection.dialect.identifier_preparer.quote(table)
        predicate = " WHERE tenant_id=:tenant" if definition["scoped"] else ""
        value = (
            connection.execute(
                text(f"""SELECT count(*) count,
          md5(COALESCE(string_agg(md5(to_jsonb(t)::text),'' ORDER BY md5(to_jsonb(t)::text)),'')) digest
          FROM {quoted} t{predicate}"""),
                {"tenant": tenant_id},
            )
            .mappings()
            .one()
        )
        result[table] = dict(value)
    tenant = connection.execute(
        text("SELECT md5(to_jsonb(t)::text) FROM tenants t WHERE id=:id"), {"id": tenant_id}
    ).scalar_one_or_none()
    if tenant is None:
        raise ValueError("Empresa no encontrada")
    revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    return {
        "tenant_id": str(tenant_id),
        "tenant_digest": tenant,
        "revision": revision,
        "tables": result,
    }


def capture(tenant_id: UUID, output: Path) -> None:
    url = make_url(normalize_database_url(get_settings().database_url or ""))
    engine = create_engine(url)
    if output.exists() or output.with_suffix(".json").exists():
        raise ValueError("El destino ya existe; usa un nombre nuevo")
    output.parent.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PGHOST": url.host or "localhost",
        "PGPORT": str(url.port or 5432),
        "PGUSER": url.username or "postgres",
        "PGDATABASE": url.database or "",
        "PGPASSWORD": url.password or "",
        "PGSSLMODE": str(url.query.get("sslmode", os.environ.get("PGSSLMODE", "prefer"))),
    }
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                snapshot = conn.execute(text("SELECT pg_export_snapshot()")).scalar_one()
                evidence = manifest(conn, tenant_id)
                completed = subprocess.run(
                    [
                        "pg_dump",
                        "--format=custom",
                        "--no-owner",
                        "--no-privileges",
                        f"--snapshot={snapshot}",
                        f"--file={output}",
                    ],
                    env=env,
                    capture_output=True,
                    timeout=1800,
                    check=False,
                )
                if completed.returncode:
                    raise RuntimeError(
                        "pg_dump falló; verifica conectividad y versión sin publicar credenciales"
                    )
        evidence["captured_at"] = datetime.now(UTC).isoformat()
        with output.open("rb") as file:
            evidence["archive_sha256"] = hashlib.file_digest(file, "sha256").hexdigest()
        output.with_suffix(".json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(
            "Respaldo y manifiesto consistentes capturados; todavía NO se certifica restauración."
        )
    finally:
        engine.dispose()


def verify(tenant_id: UUID, manifest_path: Path) -> dict:
    evidence = json.loads(manifest_path.read_text(encoding="utf-8"))
    if evidence["tenant_id"] != str(tenant_id):
        raise ValueError("El manifiesto no corresponde a esta empresa")
    source_url = make_url(normalize_database_url(get_settings().database_url or ""))
    restored = os.environ.get("RESTORED_DATABASE_URL")
    if not restored:
        raise ValueError("Configura RESTORED_DATABASE_URL con la base restaurada aislada")
    restore_url = make_url(normalize_database_url(restored))
    if (source_url.host, source_url.port, source_url.database) == (
        restore_url.host,
        restore_url.port,
        restore_url.database,
    ):
        raise ValueError("La restauración debe ser una base diferente del origen")
    restore_engine = create_engine(restore_url)
    try:
        with restore_engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                conn.execute(text("SET TRANSACTION READ ONLY"))
                actual = manifest(conn, tenant_id)
        expected = {
            key: evidence[key] for key in ("tenant_id", "tenant_digest", "revision", "tables")
        }
        if actual != expected:
            raise ValueError("La restauración no coincide con el manifiesto de origen")
        details = {
            "revision": actual["revision"],
            "tables_verified": len(actual["tables"]),
            "row_count": sum(t["count"] for t in actual["tables"].values()),
            "archive_sha256": evidence.get("archive_sha256"),
            "captured_at": evidence.get("captured_at"),
            "verification": "restored_rows_match",
        }
        source_engine = create_engine(source_url)
        try:
            with source_engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO backup_verifications(id,tenant_id,details) "
                        "VALUES (:id,:tenant,CAST(:details AS jsonb))"
                    ),
                    {"id": uuid4(), "tenant": tenant_id, "details": json.dumps(details)},
                )
        finally:
            source_engine.dispose()
        return details
    finally:
        restore_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("capture")
    command.add_argument("tenant_id", type=UUID)
    command.add_argument("--output", type=Path, required=True)
    command = commands.add_parser("verify")
    command.add_argument("tenant_id", type=UUID)
    command.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "capture":
        capture(args.tenant_id, args.output)
    else:
        print(json.dumps(verify(args.tenant_id, args.manifest)))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
import argparse
import os
import sys
import uuid

from sqlalchemy import Boolean, Column, String, Text, create_engine
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()


class ApiRoot(Base):
    __tablename__ = "opentaxii_api_root"

    id = Column(PGUUID(as_uuid=True), primary_key=True)
    default = Column(Boolean, nullable=False)
    title = Column(String(100), nullable=False, index=True)
    description = Column(Text)
    is_public = Column(Boolean, nullable=False)


def _require_db_connection(env_var: str) -> str:
    value = os.getenv(env_var)
    if not value:
        print(
            f"error: missing DB connection string in env var '{env_var}'",
            file=sys.stderr,
        )
        sys.exit(2)
    return value


def _session(db_connection: str):
    engine = create_engine(db_connection)
    return engine, sessionmaker(bind=engine)()


def _parse_uuid(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid UUID: {raw}") from exc


def cmd_list(args):
    engine, session = _session(args.db_connection)
    try:
        rows = session.query(ApiRoot).order_by(ApiRoot.title).all()
        if not rows:
            print("No API roots found.")
            return

        for row in rows:
            print(
                f"id={row.id} title={row.title!r} default={row.default} public={row.is_public} "
                f"description={row.description!r}"
            )
    finally:
        session.close()
        engine.dispose()


def cmd_create(args):
    engine, session = _session(args.db_connection)
    try:
        api_root_id = args.id or uuid.uuid4()
        if session.query(ApiRoot).filter(ApiRoot.id == api_root_id).one_or_none():
            print(f"error: api root with id {api_root_id} already exists", file=sys.stderr)
            sys.exit(1)

        if args.default:
            session.query(ApiRoot).filter(ApiRoot.default.is_(True)).update(
                {ApiRoot.default: False}
            )

        row = ApiRoot(
            id=api_root_id,
            title=args.title,
            description=args.description,
            default=args.default,
            is_public=args.public,
        )

        if args.dry_run:
            print(
                "DRY RUN create:",
                f"id={row.id} title={row.title!r} description={row.description!r} "
                f"default={row.default} public={row.is_public}",
            )
            session.rollback()
            return

        session.add(row)
        session.commit()
        print(f"Created API root {row.id}")
    finally:
        session.close()
        engine.dispose()


def cmd_update(args):
    engine, session = _session(args.db_connection)
    try:
        row = session.query(ApiRoot).filter(ApiRoot.id == args.id).one_or_none()
        if row is None:
            print(f"error: api root {args.id} not found", file=sys.stderr)
            sys.exit(1)

        changes = {}

        if args.title is not None and args.title != row.title:
            changes["title"] = (row.title, args.title)
            row.title = args.title

        if args.description is not None and args.description != row.description:
            changes["description"] = (row.description, args.description)
            row.description = args.description

        if args.public is not None and args.public != row.is_public:
            changes["is_public"] = (row.is_public, args.public)
            row.is_public = args.public

        if args.default is not None and args.default != row.default:
            if args.default:
                session.query(ApiRoot).filter(ApiRoot.default.is_(True)).update(
                    {ApiRoot.default: False}
                )
            changes["default"] = (row.default, args.default)
            row.default = args.default

        if not changes:
            print("No changes to apply.")
            return

        if args.dry_run:
            print(f"DRY RUN update for {row.id}:")
            for key, (old, new) in changes.items():
                print(f"  {key}: {old!r} -> {new!r}")
            session.rollback()
            return

        session.commit()
        print(f"Updated API root {row.id}")
    finally:
        session.close()
        engine.dispose()


def cmd_delete(args):
    engine, session = _session(args.db_connection)
    try:
        row = session.query(ApiRoot).filter(ApiRoot.id == args.id).one_or_none()
        if row is None:
            print(f"error: api root {args.id} not found", file=sys.stderr)
            sys.exit(1)

        if not args.yes:
            print(
                "error: deletion requires --yes to confirm (cascade may delete related rows)",
                file=sys.stderr,
            )
            sys.exit(2)

        if args.dry_run:
            print(f"DRY RUN delete: id={row.id} title={row.title!r}")
            session.rollback()
            return

        session.delete(row)
        session.commit()
        print(f"Deleted API root {args.id}")
    finally:
        session.close()
        engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone OpenTAXII API root manager (PostgreSQL)."
    )
    parser.add_argument(
        "--db-connection-env",
        default="OPENTAXII_DB_CONNECTION",
        help="Environment variable containing SQLAlchemy DB connection string",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List API roots")
    p_list.set_defaults(func=cmd_list)

    p_create = sub.add_parser("create", help="Create an API root")
    p_create.add_argument("--id", type=_parse_uuid, help="UUID to assign")
    p_create.add_argument("--title", required=True, help="API root title")
    p_create.add_argument("--description", help="API root description")
    p_create.add_argument("--default", action="store_true", help="Set as default")
    p_create.add_argument("--public", action="store_true", help="Set public")
    p_create.add_argument("--dry-run", action="store_true", help="Show change only")
    p_create.set_defaults(func=cmd_create)

    p_update = sub.add_parser("update", help="Update an existing API root")
    p_update.add_argument("id", type=_parse_uuid, help="API root UUID")
    p_update.add_argument("--title", help="New title")
    p_update.add_argument("--description", help="New description")
    p_update.add_argument(
        "--public",
        type=lambda v: {"true": True, "false": False}[v.lower()],
        choices=[True, False],
        help="Set public flag (true|false)",
    )
    p_update.add_argument(
        "--default",
        type=lambda v: {"true": True, "false": False}[v.lower()],
        choices=[True, False],
        help="Set default flag (true|false)",
    )
    p_update.add_argument("--dry-run", action="store_true", help="Show change only")
    p_update.set_defaults(func=cmd_update)

    p_delete = sub.add_parser("delete", help="Delete an API root")
    p_delete.add_argument("id", type=_parse_uuid, help="API root UUID")
    p_delete.add_argument("--yes", action="store_true", help="Confirm deletion")
    p_delete.add_argument("--dry-run", action="store_true", help="Show change only")
    p_delete.set_defaults(func=cmd_delete)

    return parser


def main():
    parser = _build_parser()
    args = parser.parse_args()
    args.db_connection = _require_db_connection(args.db_connection_env)
    args.func(args)


if __name__ == "__main__":
    main()

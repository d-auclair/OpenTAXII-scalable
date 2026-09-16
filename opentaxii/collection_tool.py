#!/usr/bin/env python3
import argparse
import os
import sys
import uuid

from sqlalchemy import Boolean, Column, ForeignKey, String, Text, UniqueConstraint, create_engine
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, sessionmaker

Base = declarative_base()


class ApiRoot(Base):
    __tablename__ = "opentaxii_api_root"

    id = Column(PGUUID(as_uuid=True), primary_key=True)
    default = Column(Boolean, nullable=False)
    title = Column(String(100), nullable=False, index=True)
    description = Column(Text)
    is_public = Column(Boolean, nullable=False)

    collections = relationship("Collection", back_populates="api_root")


class Collection(Base):
    __tablename__ = "opentaxii_collection"

    id = Column(PGUUID(as_uuid=True), primary_key=True)
    api_root_id = Column(PGUUID(as_uuid=True), ForeignKey("opentaxii_api_root.id", ondelete="CASCADE"))
    title = Column(String(100), nullable=False, index=True)
    description = Column(Text)
    alias = Column(String(100), nullable=True)
    is_public = Column(Boolean, nullable=False)
    is_public_write = Column(Boolean, nullable=False)

    api_root = relationship("ApiRoot", back_populates="collections")

    __table_args__ = (UniqueConstraint(api_root_id, alias),)


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


def _parse_bool(raw: str) -> bool:
    values = {"true": True, "false": False}
    key = raw.lower()
    if key not in values:
        raise argparse.ArgumentTypeError("expected true|false")
    return values[key]


def _ensure_api_root_exists(session, api_root_id: uuid.UUID):
    row = session.query(ApiRoot).filter(ApiRoot.id == api_root_id).one_or_none()
    if row is None:
        print(f"error: api root {api_root_id} not found", file=sys.stderr)
        sys.exit(1)


def cmd_list(args):
    engine, session = _session(args.db_connection)
    query = session.query(Collection)

    if args.api_root_id is not None:
        query = query.filter(Collection.api_root_id == args.api_root_id)

    rows = query.order_by(Collection.title).all()
    if not rows:
        print("No collections found.")
        return

    for row in rows:
        print(
            f"id={row.id} api_root_id={row.api_root_id} title={row.title!r} "
            f"alias={row.alias!r} public={row.is_public} public_write={row.is_public_write} "
            f"description={row.description!r}"
        )


def cmd_create(args):
    engine, session = _session(args.db_connection)
    _ensure_api_root_exists(session, args.api_root_id)

    collection_id = args.id or uuid.uuid4()
    if session.query(Collection).filter(Collection.id == collection_id).one_or_none():
        print(f"error: collection with id {collection_id} already exists", file=sys.stderr)
        sys.exit(1)

    if args.alias is not None:
        existing_alias = (
            session.query(Collection)
            .filter(
                Collection.api_root_id == args.api_root_id,
                Collection.alias == args.alias,
            )
            .one_or_none()
        )
        if existing_alias is not None:
            print(
                f"error: alias {args.alias!r} already exists in api root {args.api_root_id}",
                file=sys.stderr,
            )
            sys.exit(1)

    row = Collection(
        id=collection_id,
        api_root_id=args.api_root_id,
        title=args.title,
        description=args.description,
        alias=args.alias,
        is_public=args.public,
        is_public_write=args.public_write,
    )

    if args.dry_run:
        print(
            "DRY RUN create:",
            f"id={row.id} api_root_id={row.api_root_id} title={row.title!r} "
            f"description={row.description!r} alias={row.alias!r} "
            f"public={row.is_public} public_write={row.is_public_write}",
        )
        session.rollback()
        return

    session.add(row)
    session.commit()
    print(f"Created collection {row.id}")


def cmd_update(args):
    engine, session = _session(args.db_connection)

    row = session.query(Collection).filter(Collection.id == args.id).one_or_none()
    if row is None:
        print(f"error: collection {args.id} not found", file=sys.stderr)
        sys.exit(1)

    changes = {}

    if args.api_root_id is not None and args.api_root_id != row.api_root_id:
        _ensure_api_root_exists(session, args.api_root_id)
        changes["api_root_id"] = (row.api_root_id, args.api_root_id)
        row.api_root_id = args.api_root_id

    if args.title is not None and args.title != row.title:
        changes["title"] = (row.title, args.title)
        row.title = args.title

    if args.description is not None and args.description != row.description:
        changes["description"] = (row.description, args.description)
        row.description = args.description

    if args.alias is not None and args.alias != row.alias:
        if args.alias != "":
            existing_alias = (
                session.query(Collection)
                .filter(
                    Collection.api_root_id == row.api_root_id,
                    Collection.alias == args.alias,
                    Collection.id != row.id,
                )
                .one_or_none()
            )
            if existing_alias is not None:
                print(
                    f"error: alias {args.alias!r} already exists in api root {row.api_root_id}",
                    file=sys.stderr,
                )
                sys.exit(1)
            new_alias = args.alias
        else:
            new_alias = None
        changes["alias"] = (row.alias, new_alias)
        row.alias = new_alias

    if args.public is not None and args.public != row.is_public:
        changes["is_public"] = (row.is_public, args.public)
        row.is_public = args.public

    if args.public_write is not None and args.public_write != row.is_public_write:
        changes["is_public_write"] = (row.is_public_write, args.public_write)
        row.is_public_write = args.public_write

    if not changes:
        print("No changes to apply.")
        return

    if args.dry_run:
        print(f"DRY RUN update for {row.id}:")
        for key, (old, new) in changes.items():
            print(f"  {key}: {old!r} -> {new!r}")
        session.rollback()
        return

    try:
        session.commit()
    except Exception as exc:
        session.rollback()
        print(f"error: failed to update collection: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Updated collection {row.id}")


def cmd_delete(args):
    engine, session = _session(args.db_connection)

    row = session.query(Collection).filter(Collection.id == args.id).one_or_none()
    if row is None:
        print(f"error: collection {args.id} not found", file=sys.stderr)
        sys.exit(1)

    if not args.yes:
        print(
            "error: deletion requires --yes to confirm (may cascade-delete related rows)",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.dry_run:
        print(
            f"DRY RUN delete: id={row.id} api_root_id={row.api_root_id} title={row.title!r} alias={row.alias!r}"
        )
        session.rollback()
        return

    session.delete(row)
    session.commit()
    print(f"Deleted collection {args.id}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Standalone OpenTAXII collection manager (PostgreSQL)."
    )
    parser.add_argument(
        "--db-connection-env",
        default="OPENTAXII_DB_CONNECTION",
        help="Environment variable containing SQLAlchemy DB connection string",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List collections")
    p_list.add_argument("--api-root-id", type=_parse_uuid, help="Filter by API root UUID")
    p_list.set_defaults(func=cmd_list)

    p_create = sub.add_parser("create", help="Create a collection")
    p_create.add_argument("--id", type=_parse_uuid, help="UUID to assign")
    p_create.add_argument("--api-root-id", type=_parse_uuid, required=True, help="Parent API root UUID")
    p_create.add_argument("--title", required=True, help="Collection title")
    p_create.add_argument("--description", help="Collection description")
    p_create.add_argument("--alias", help="Collection alias")
    p_create.add_argument("--public", action="store_true", help="Set public read")
    p_create.add_argument("--public-write", action="store_true", help="Set public write")
    p_create.add_argument("--dry-run", action="store_true", help="Show change only")
    p_create.set_defaults(func=cmd_create)

    p_update = sub.add_parser("update", help="Update a collection")
    p_update.add_argument("id", type=_parse_uuid, help="Collection UUID")
    p_update.add_argument("--api-root-id", type=_parse_uuid, help="Move to another API root UUID")
    p_update.add_argument("--title", help="New title")
    p_update.add_argument("--description", help="New description")
    p_update.add_argument("--alias", help="New alias (use empty string to clear)")
    p_update.add_argument("--public", type=_parse_bool, help="Set public read flag (true|false)")
    p_update.add_argument("--public-write", type=_parse_bool, help="Set public write flag (true|false)")
    p_update.add_argument("--dry-run", action="store_true", help="Show change only")
    p_update.set_defaults(func=cmd_update)

    p_delete = sub.add_parser("delete", help="Delete a collection")
    p_delete.add_argument("id", type=_parse_uuid, help="Collection UUID")
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

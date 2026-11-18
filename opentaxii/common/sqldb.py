import time
from typing import ClassVar, Type

from sqlalchemy.exc import OperationalError

from opentaxii.sqldb_helper import SQLAlchemyDB

try:
    from sqlalchemy.orm import DeclarativeMeta  # type: ignore[attr-defined]
except ImportError:
    from sqlalchemy.ext.declarative import DeclarativeMeta


class BaseSQLDatabaseAPI:
    BASEMODEL: ClassVar[Type[DeclarativeMeta]]

    def __init__(self, db_connection, create_tables=False, **engine_parameters):
        super().__init__()
        self.db = SQLAlchemyDB(
            db_connection,
            self.BASEMODEL,
            session_options={
                "autocommit": False,
                "autoflush": True,
            },
            **engine_parameters,
        )
        if create_tables:
            self.db.create_all_tables()

    def init_app(self, app):
        self.db.init_app(app)

    @staticmethod
    def _is_retryable(error):
        original = getattr(error, "orig", error)
        sqlstate = str(
            getattr(original, "pgcode", None)
            or getattr(original, "sqlstate", None)
            or ""
        )
        if sqlstate in ("40001", "40P01"):
            return True
        args = getattr(original, "args", ())
        if args and args[0] in (1205, 1213):
            return True
        message = str(original).lower()
        return "database is locked" in message or "database is busy" in message

    def _commit_with_retry(self, max_attempts=3, base_delay=0.05):
        attempts = 0
        while True:
            try:
                self.db.session.commit()
                return
            except OperationalError as error:
                self.db.session.rollback()
                attempts += 1
                pgcode = str(getattr(error.orig, "pgcode", "") or "")
                if pgcode not in ("40001", "40P01") or attempts >= max_attempts:
                    raise
                time.sleep(base_delay * attempts)
            except Exception:
                self.db.session.rollback()
                raise
            finally:
                self.db.session.remove()

    def _run_with_retry(self, callback, max_attempts=3, base_delay=0.05):
        attempts = 0
        while True:
            try:
                result = callback()
                self.db.session.commit()
                return result
            except OperationalError as error:
                self.db.session.rollback()
                attempts += 1
                pgcode = str(getattr(error.orig, "pgcode", "") or "")
                if pgcode not in ("40001", "40P01") or attempts >= max_attempts:
                    raise
                time.sleep(base_delay * attempts)
            except Exception:
                self.db.session.rollback()
                raise
            finally:
                self.db.session.remove()

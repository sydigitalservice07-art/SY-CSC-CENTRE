"""
SY CSC CENTRE - database connection module
==========================================
One small, portable layer used by every module.

* Production engine : MySQL / MariaDB through PyMySQL  (DB_ENGINE=mysql)
* Test / demo engine : SQLite through the standard library (DB_ENGINE=sqlite)

Both engines are driven by exactly the same SQL.  Placeholders are written in
MySQL style (%s) and translated to (?) automatically for SQLite, so no module
ever needs to know which engine is running.

Money is handled with decimal.Decimal everywhere - never float arithmetic.
"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

try:                                            # MySQL driver (production)
    import pymysql
    import pymysql.cursors
except Exception:                               # pragma: no cover
    pymysql = None

TWO_PLACES = Decimal("0.01")

# explicit SQLite adapters (Python 3.12 deprecates the implicit ones)
sqlite3.register_adapter(datetime, lambda value: value.strftime("%Y-%m-%d %H:%M:%S.%f"))
sqlite3.register_adapter(date, lambda value: value.isoformat())
sqlite3.register_converter("DATE", lambda raw: date.fromisoformat(raw.decode()[:10]))
sqlite3.register_converter(
    "DATETIME", lambda raw: datetime.fromisoformat(raw.decode().replace(" ", "T", 1)))
ZERO = Decimal("0.00")


# --------------------------------------------------------------------- money
def D(value):
    """Safely convert anything (None, str, float, Decimal) to a 2-dp Decimal."""
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    try:
        return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
    except Exception:
        return ZERO


def fmt_money(value, symbol=""):
    """Indian style grouping: 1,23,456.00"""
    amount = D(value)
    negative = amount < 0
    amount = abs(amount)
    whole, _, frac = str(amount).partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    text = whole + "." + (frac or "00")
    return ("-" if negative else "") + (symbol + " " if symbol else "") + text


class DatabaseError(Exception):
    pass


class Connection(object):
    """Thin wrapper giving dict rows and dialect translation."""

    def __init__(self, raw, engine):
        self.raw = raw
        self.engine = engine

    # ------------------------------------------------------------ internals
    def _sql(self, sql):
        if self.engine == "sqlite":
            return sql.replace("%s", "?")
        return sql

    def _cursor(self):
        return self.raw.cursor()

    def _params(self, params):
        """SQLite cannot bind Decimal / bool directly - adapt them here."""
        if not params:
            return ()
        if self.engine != "sqlite":
            return tuple(params)
        out = []
        for value in params:
            if isinstance(value, Decimal):
                out.append(format(value, "f"))
            elif isinstance(value, bool):
                out.append(1 if value else 0)
            else:
                out.append(value)
        return tuple(out)

    # -------------------------------------------------------------- reading
    def query(self, sql, params=()):
        cur = self._cursor()
        try:
            cur.execute(self._sql(sql), self._params(params))
            if not cur.description:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            cur.close()

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql, params=(), default=None):
        row = self.query_one(sql, params)
        if not row:
            return default
        value = list(row.values())[0]
        return default if value is None else value

    # -------------------------------------------------------------- writing
    def execute(self, sql, params=()):
        """Returns (lastrowid, rowcount)."""
        cur = self._cursor()
        try:
            cur.execute(self._sql(sql), self._params(params))
            return getattr(cur, "lastrowid", None), cur.rowcount
        finally:
            cur.close()

    def executemany(self, sql, seq):
        cur = self._cursor()
        try:
            cur.executemany(self._sql(sql), [self._params(row) for row in seq])
            return cur.rowcount
        finally:
            cur.close()

    # ---------------------------------------------------------- transactions
    def begin(self):
        if self.engine == "sqlite":
            self.raw.execute("BEGIN IMMEDIATE")
        else:
            self.raw.begin()

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        try:
            self.raw.close()
        except Exception:
            pass

    # --------------------------------------------------------------- helper
    def lock_clause(self):
        """MySQL row lock; SQLite locks through BEGIN IMMEDIATE instead."""
        return " FOR UPDATE" if self.engine == "mysql" else ""


class Database(object):
    def __init__(self, engine="mysql", host="127.0.0.1", port=3306, user="",
                 password="", database="", charset="utf8mb4", sqlite_path=""):
        self.engine = (engine or "mysql").lower()
        self.host = host
        self.port = int(port or 3306)
        self.user = user
        self.password = password
        self.database = database
        self.charset = charset or "utf8mb4"
        self.sqlite_path = sqlite_path

    # ------------------------------------------------------------- connect
    def connect(self, with_database=True):
        if self.engine == "sqlite":
            path = self.sqlite_path or ":memory:"
            if path != ":memory:":
                folder = os.path.dirname(path)
                if folder and not os.path.isdir(folder):
                    os.makedirs(folder)
            raw = sqlite3.connect(
                path, timeout=20, isolation_level=None,
                detect_types=sqlite3.PARSE_DECLTYPES)
            raw.execute("PRAGMA foreign_keys = ON")
            raw.execute("PRAGMA journal_mode = WAL")
            return Connection(raw, "sqlite")

        if pymysql is None:                      # pragma: no cover
            raise DatabaseError(
                "PyMySQL is not installed. Run: pip install -r requirements.txt")
        raw = pymysql.connect(
            host=self.host, port=self.port, user=self.user,
            password=self.password, charset=self.charset,
            database=self.database if with_database else None,
            autocommit=False,
        )
        return Connection(raw, "mysql")

    # ---------------------------------------------------------- context mgr
    @contextmanager
    def connection(self):
        """Auto-commit connection for read-mostly or single-statement work."""
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        """All-or-nothing unit of work."""
        conn = self.connect()
        try:
            conn.begin()
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


# ------------------------------------------------------------------ singleton
_db = None


def init_app(app):
    """Create the shared Database object from a Flask app config."""
    global _db
    _db = Database(
        engine=app.config.get("DB_ENGINE", "mysql"),
        host=app.config.get("DB_HOST"),
        port=app.config.get("DB_PORT"),
        user=app.config.get("DB_USER"),
        password=app.config.get("DB_PASSWORD"),
        database=app.config.get("DB_NAME"),
        charset=app.config.get("DB_CHARSET", "utf8mb4"),
        sqlite_path=app.config.get("SQLITE_PATH"),
    )
    return _db


def get_db():
    if _db is None:
        raise DatabaseError("Database not initialised - call db.init_app(app) first")
    return _db

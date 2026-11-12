import time

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker


def make_engine(url):
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(pool_size=5, max_overflow=5, connect_args={"connect_timeout": 5})
    return create_engine(url, **kwargs)


def make_sessions(engine):
    return sessionmaker(engine, expire_on_commit=False)


def db_now(session):
    if session.bind.dialect.name == "postgresql":
        return float(session.scalar(select(func.extract("epoch", func.clock_timestamp()))))
    return time.time()  # SQLite is used for unit tests only.

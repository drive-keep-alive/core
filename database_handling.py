from contextlib import contextmanager

from sqlmodel import SQLModel, Session, create_engine

DATABASE_URL = "./database/drives.db"
engine = create_engine(f"sqlite:///{DATABASE_URL}", echo=False)


def init_db():
    """Create tables if missing. Call once at startup."""
    SQLModel.metadata.create_all(engine)


@contextmanager
def session_scope():
    """Yield a session; commits on clean exit, rolls back on error."""
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

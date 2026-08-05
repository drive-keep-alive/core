from sqlmodel import SQLModel,Session, Field, create_engine

DATABASE_URL = "./database/drives.db"
engine = create_engine(
    f"sqlite:///{DATABASE_URL}", echo=True
)

def get_session():
    with Session(engine) as session:
        session

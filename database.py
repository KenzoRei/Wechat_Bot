from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
import config

engine = create_engine(config.DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """
    FastAPI dependency. Yields one DB session per request, always closes it.
    Usage in route: db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

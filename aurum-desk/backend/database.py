from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import models
import os

# Create SQLite database
SQLALCHEMY_DATABASE_URL = "sqlite:///./aurum_desk.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

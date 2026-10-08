from sqlalchemy.orm import Session
import models, schemas

def get_candles(db: Session, symbol: str, timeframe: str, limit: int = 100):
    return db.query(models.Candle)\
        .filter(models.Candle.symbol == symbol, models.Candle.timeframe == timeframe)\
        .order_by(models.Candle.timestamp.desc())\
        .limit(limit)\
        .all()

def create_candle(db: Session, candle: schemas.CandleCreate):
    db_candle = models.Candle(**candle.dict())
    db.add(db_candle)
    db.commit()
    db.refresh(db_candle)
    return db_candle

def bulk_insert_candles(db: Session, candles: list[schemas.CandleCreate]):
    for c in candles:
        existing = db.query(models.Candle).filter(
            models.Candle.symbol == c.symbol,
            models.Candle.timeframe == c.timeframe,
            models.Candle.timestamp == c.timestamp
        ).first()
        if not existing:
            db.add(models.Candle(**c.dict()))
        else:
            existing.open = c.open
            existing.high = c.high
            existing.low = c.low
            existing.close = c.close
            existing.volume = c.volume
            existing.is_closed = c.is_closed
    db.commit()

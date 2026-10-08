from pydantic import BaseModel
from typing import List, Optional

class CandleBase(BaseModel):
    symbol: str
    timeframe: str
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    is_closed: bool

class CandleCreate(CandleBase):
    pass

class Candle(CandleBase):
    id: int

    class Config:
        orm_mode = True

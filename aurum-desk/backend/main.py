from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import models, schemas, crud, bitget_data
from database import engine, get_db

models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Aurum Desk API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health_check():
    return {"status": "ok", "service": "Aurum Desk Backend"}

@app.get("/api/v1/candles/{symbol}/{timeframe}", response_model=list[schemas.Candle])
def get_candles_endpoint(symbol: str, timeframe: str, limit: int = 100, db: Session = Depends(get_db)):
    # 1. Trả về nến từ DB
    candles = crud.get_candles(db, symbol, timeframe, limit)
    return candles

@app.post("/api/v1/candles/sync")
def sync_candles(symbol: str = "XAUUSDT", timeframe: str = "15M", limit: int = 100, db: Session = Depends(get_db)):
    # Lấy dữ liệu mới nhất từ Bitget
    new_candles = bitget_data.fetch_candles(symbol, timeframe, limit)
    if new_candles:
        crud.bulk_insert_candles(db, new_candles)
        return {"status": "success", "synced_count": len(new_candles)}
    return {"status": "error", "message": "Failed to sync"}


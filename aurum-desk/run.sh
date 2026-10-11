#!/bin/bash
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "🚀 Starting Aurum Desk Backend (FastAPI on port 8000)..."
cd "$ROOT_DIR/backend"
export PYTHONPATH="$ROOT_DIR/backend"
"$ROOT_DIR/backend/venv/bin/python" -m uvicorn main:app --reload --port 8000 &
BACKEND_PID=$!

echo "🚀 Starting Aurum Desk Frontend (Vite on port 5174)..."
cd "$ROOT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

cleanup() {
  echo "Shutting down services..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null
  exit 0
}

trap cleanup SIGINT SIGTERM

wait

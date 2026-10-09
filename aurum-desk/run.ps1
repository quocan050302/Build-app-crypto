# AURUM DESK - Windows Startup Script
# Khoi dong toan bo he thong local XAUUSDT Research & Paper Trading

$ScriptDir = $PSScriptRoot
Write-Host "=====================================================" -ForegroundColor DarkYellow
Write-Host "   AURUM DESK - HE THONG SMC/ICT & PAPER TRADING   " -ForegroundColor Yellow
Write-Host "=====================================================" -ForegroundColor DarkYellow

# 1. Kiem tra & Chuan bi Backend
$BackendDir = Join-Path $ScriptDir "backend"
$VenvPython = Join-Path $BackendDir "venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Host "[*] Dang khoi tao moi virtual environment (venv)..." -ForegroundColor Cyan
    python -m venv "$BackendDir\venv"
    & "$VenvPython" -m pip install --upgrade pip
    & "$VenvPython" -m pip install -r "$BackendDir\requirements.txt"
}

# Chay migration neu can
Write-Host "[*] Kiem tra migration co so du lieu SQLite..." -ForegroundColor Cyan
$env:PYTHONPATH = $BackendDir
& "$VenvPython" "$BackendDir\migrate.py"

# 2. Kiem tra Frontend
$FrontendDir = Join-Path $ScriptDir "frontend"
if (-not (Test-Path "$FrontendDir\node_modules")) {
    Write-Host "[*] Dang cai dat npm packages cho Frontend..." -ForegroundColor Cyan
    Start-Process -FilePath "npm" -ArgumentList "install" -WorkingDirectory $FrontendDir -Wait
}

# 3. Khoi chay Backend Server (Port 8000)
Write-Host "[+] Khoi chay Backend FastAPI tai http://127.0.0.1:8000..." -ForegroundColor Green
Start-Process powershell -WorkingDirectory $BackendDir -ArgumentList "-NoExit", "-Command", "`$env:PYTHONPATH='.'; .\venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000"

# Cho 2 giay de backend khoi dong
Start-Sleep -Seconds 2

# 4. Khoi chay Frontend Dev Server (Port 5173)
Write-Host "[+] Khoi chay Frontend Vite tai http://localhost:5173..." -ForegroundColor Green
Start-Process powershell -WorkingDirectory $FrontendDir -ArgumentList "-NoExit", "-Command", "npm run dev"

Write-Host "=====================================================" -ForegroundColor DarkYellow
Write-Host "   AURUM DESK da san sang! Vui long mo trinh duyet   " -ForegroundColor Yellow
Write-Host "   Dia chi: http://localhost:5173                    " -ForegroundColor White
Write-Host "=====================================================" -ForegroundColor DarkYellow

# AURUM DESK - Windows Startup Script
# Khoi dong toan bo he thong local XAUUSDT Research & Paper Trading

$ScriptDir = $PSScriptRoot
Write-Host "=====================================================" -ForegroundColor DarkYellow
Write-Host "   AURUM DESK - HE THONG SMC/ICT & PAPER TRADING   " -ForegroundColor Yellow
Write-Host "=====================================================" -ForegroundColor DarkYellow

# 0. Don dep cac tien trinh cu cua Aurum Desk dang chay (Port 8000 & 5174) neu co
Write-Host "[*] Kiem tra port 8000 va 5174..." -ForegroundColor Cyan
$existingPorts = @(8000, 5174)
foreach ($port in $existingPorts) {
    $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($conns) {
        $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -gt 4 }
        foreach ($procId in $pids) {
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($proc) {
                $cmdLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue).CommandLine
                $isAurum = ($cmdLine -and ($cmdLine -match "aurum-desk" -or $cmdLine -match "uvicorn" -or $cmdLine -match "vite"))
                if ($isAurum) {
                    Write-Host "    -> Dung process Aurum Desk dang chiem port $port (PID: $procId)..." -ForegroundColor Yellow
                    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                } else {
                    Write-Host "    [!] Canh bao: Port $port dang bi chiem boi tien trinh khong phai Aurum (PID: $procId, Name: $($proc.ProcessName))." -ForegroundColor Red
                }
            }
        }
    }
}
Start-Sleep -Milliseconds 800

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
Start-Process powershell -WorkingDirectory $BackendDir -ArgumentList "-NoExit", "-Command", "`$env:PYTHONPATH='.'; & '$VenvPython' -m uvicorn main:app --reload --host 127.0.0.1 --port 8000"

# Cho 2 giay de backend khoi dong
Start-Sleep -Seconds 2

# 4. Khoi chay Frontend Dev Server (Port 5174)
Write-Host "[+] Khoi chay Frontend Vite tai http://localhost:5174..." -ForegroundColor Green
Start-Process powershell -WorkingDirectory $FrontendDir -ArgumentList "-NoExit", "-Command", "npm run dev"

Write-Host "=====================================================" -ForegroundColor DarkYellow
Write-Host "   AURUM DESK da san sang! Vui long mo trinh duyet   " -ForegroundColor Yellow
Write-Host "   Dia chi: http://localhost:5174                    " -ForegroundColor White
Write-Host "=====================================================" -ForegroundColor DarkYellow

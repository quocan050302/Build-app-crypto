# Navigate to aurum-desk folder
$ScriptDir = $PSScriptRoot

# Start Backend
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$ScriptDir\backend'; .\venv\Scripts\python.exe -m uvicorn main:app --reload --port 8000"

# Start Frontend
Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$ScriptDir\frontend'; npm run dev"

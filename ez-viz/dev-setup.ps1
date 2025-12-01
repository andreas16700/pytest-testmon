#Requires -Version 5.1
$ErrorActionPreference = "Stop"


function Test-DataPresent {
    param([string]$Path = ".\testmon_data")
    if (-not (Test-Path $Path)) { return $false }
    $files = Get-ChildItem $Path -Recurse -Force -File -ErrorAction SilentlyContinue
    return ($files | Measure-Object).Count -gt 0
}

$PythonCmd = "py"

if (-not (Test-DataPresent)) {
    & $PythonCmd ".\sync_prod_data.py"
}
else {
  
    $resync = Read-Host "🔄 Re-sync from production? (y/N)"
    if ($resync -match '^[Yy]$') {
        & $PythonCmd ".\sync_prod_data.py"
    }
}




$backend = Start-Process -FilePath $PythonCmd -ArgumentList ".\app.py" -PassThru -NoNewWindow
Start-Sleep -Seconds 3


Push-Location client
$frontend = Start-Process -FilePath "npm" -ArgumentList "run","dev" -PassThru -NoNewWindow
Pop-Location


$script:backend = $backend
$script:frontend = $frontend

$handler = {
    param($sender, $eventArgs)
    $eventArgs.Cancel = $true
    Write-Host "`n🛑 Stopping servers..."
    try {
        if ($script:backend -and -not $script:backend.HasExited) { Stop-Process -Id $script:backend.Id -ErrorAction SilentlyContinue }
        if ($script:frontend -and -not $script:frontend.HasExited) { Stop-Process -Id $script:frontend.Id -ErrorAction SilentlyContinue }
    } catch {}
  
    exit
}
[Console]::CancelKeyPress += $handler

try {
    Wait-Process -Id @($backend.Id, $frontend.Id)
}
finally {
    if ($backend -and -not $backend.HasExited) { Stop-Process -Id $backend.Id -ErrorAction SilentlyContinue }
    if ($frontend -and -not $frontend.HasExited) { Stop-Process -Id $frontend.Id -ErrorAction SilentlyContinue }
    [Console]::CancelKeyPress -= $handler
}

param(
    [string]$Email = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvCandidates = @(
    (Join-Path $ProjectRoot "venv\Scripts\python.exe"),
    (Join-Path $ProjectRoot ".venv\Scripts\python.exe")
)
$PythonPath = $VenvCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
$FlaskArgs = @("-m", "flask", "--app", "run.py", "recover-admin")
if ($Email) {
    $FlaskArgs += @("--email", $Email)
}

Push-Location $ProjectRoot
try {
    Write-Host "SulitShelf PH local administrator recovery" -ForegroundColor Cyan
    Write-Host "The new password is requested securely and is not stored by this script."
    if ($PythonPath) {
        & $PythonPath @FlaskArgs
    }
    else {
        & py -3.12 @FlaskArgs
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Administrator recovery failed. Read the error above before trying again."
    }
}
finally {
    Pop-Location
}

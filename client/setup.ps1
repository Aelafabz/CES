$ErrorActionPreference = 'Stop'
$clientRoot = $PSScriptRoot
$clientPython = Join-Path $clientRoot '.venv\Scripts\python.exe'
try {
    if (-not (Test-Path -LiteralPath $clientPython)) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3 -m venv (Join-Path $clientRoot '.venv')
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            & python -m venv (Join-Path $clientRoot '.venv')
        } else {
            throw 'Install Python 3.10 or newer with Tcl/Tk support, then run setup again.'
        }
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the client Python environment.' }
    }
    & $clientPython -m pip install -r (Join-Path $clientRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    $clientEnv = Join-Path $clientRoot '.env'
    if (-not (Test-Path -LiteralPath $clientEnv)) {
        Copy-Item -LiteralPath (Join-Path $clientRoot '.env.example') -Destination $clientEnv
    }
    & $clientPython (Join-Path $clientRoot 'run_client.py') --check
    if ($LASTEXITCODE -ne 0) { throw 'Client setup check failed.' }
    Write-Host 'Setup complete. Edit .env for this machine, then run start-client.cmd.'
} catch {
    Write-Error $_
    exit 1
}

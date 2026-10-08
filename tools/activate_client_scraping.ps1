param([Parameter(Mandatory=$true)][int]$ReceiverProcessId, [switch]$TestLocalClient)
$ErrorActionPreference = 'Stop'
$projectRoot = 'C:\CES'
$hostPython = Join-Path $projectRoot 'venv\Scripts\python.exe'
$clientPython = 'C:\client\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $clientPython)) { throw 'Run client setup.cmd before activating the scraping agent.' }
$receiverListener = netstat -ano -p tcp | Select-String -Pattern ":8000\s+.*LISTENING\s+$ReceiverProcessId\s*$"
if (-not $receiverListener) { throw 'The selected process is no longer the Maraki receiver on port 8000.' }
& $hostPython -B (Join-Path $projectRoot 'tools\install_client_scraping_agent.py')
if ($LASTEXITCODE -ne 0) { throw 'Client update installation failed.' }
& $clientPython -B 'C:\client\run_client.py' --check
if ($LASTEXITCODE -ne 0) { throw 'Client setup check failed.' }
& taskkill /PID $ReceiverProcessId /F
if ($LASTEXITCODE -ne 0) { throw 'Could not stop the existing receiver.' }
$newReceiver = Start-Process -FilePath $hostPython -ArgumentList '-u','-B',(Join-Path $projectRoot 'host\mrk-host\mrk_receiver.py') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $projectRoot 'host\switchboard\logs\mrk-control.out.log') -RedirectStandardError (Join-Path $projectRoot 'host\switchboard\logs\mrk-control.err.log') -PassThru
Write-Output "Receiver started: $($newReceiver.Id). Start the scraping agent on each client PC."
if ($TestLocalClient) {
    $agentStateDirectory = 'C:\client\mrk-agent-data'
    New-Item -ItemType Directory -Force -Path $agentStateDirectory | Out-Null
    $newAgent = Start-Process -FilePath $clientPython -ArgumentList '-u','-B','C:\client\mrk_agent.py' -WorkingDirectory 'C:\client' -WindowStyle Hidden -RedirectStandardOutput (Join-Path $agentStateDirectory 'agent.log') -RedirectStandardError (Join-Path $agentStateDirectory 'agent.err.log') -PassThru
    Write-Output "Local test agent started: $($newAgent.Id) (shown as Host PC in the switchboard)."
}

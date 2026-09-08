param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$studioPython = Join-Path $studioRoot '.venv\Scripts\python.exe'
$studioLogs = Join-Path $studioRoot 'logs'
New-Item -ItemType Directory -Path $studioLogs -Force | Out-Null
if (-not (Test-Path -LiteralPath $studioPython -PathType Leaf)) { throw 'Run Setup.ps1 first to install the local runtime.' }
if (-not (Test-Path -LiteralPath (Join-Path $studioRoot 'dist\index.html') -PathType Leaf)) { throw 'Run Setup.ps1 first to build the editor.' }

function Test-StudioResponse {
    param($Health)
    return ($null -ne $Health -and $Health.version -match '^1\.\d+\.\d+$' -and
            $Health.token -is [string] -and $Health.token.Length -ge 32 -and
            $null -ne $Health.project -and $Health.project.schema_version -eq 1 -and
            $Health.project.id -is [string])
}

$studioOnline = $false
try {
    $studioHealth = Invoke-RestMethod 'http://127.0.0.1:8766/api/bootstrap' -TimeoutSec 2
    $studioOnline = Test-StudioResponse $studioHealth
    if (-not $studioOnline) { throw 'Port 8766 answered, but it did not identify itself as H3 Prompt Studio.' }
} catch {
    if ($_.Exception.Message -like 'Port 8766 answered*') { throw }
    if ($null -ne $_.Exception.Response) {
        throw 'A server on port 8766 returned an error instead of the Studio workspace. Check its existing logs before starting another copy.'
    }
}
if (-not $studioOnline) {
    $studioStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $studioOutLog = Join-Path $studioLogs "server-$studioStamp.out.log"
    $studioErrorLog = Join-Path $studioLogs "server-$studioStamp.err.log"
    $studioProcess = Start-Process -FilePath $studioPython -ArgumentList @('-X','utf8','-m','uvicorn','backend.app:app','--host','127.0.0.1','--port','8766','--no-access-log') -WorkingDirectory $studioRoot -WindowStyle Hidden -RedirectStandardOutput $studioOutLog -RedirectStandardError $studioErrorLog -PassThru
    $studioProcess.Id | Set-Content -LiteralPath (Join-Path $studioLogs 'server.pid')
    @{ pid = $studioProcess.Id; stdout = $studioOutLog; stderr = $studioErrorLog } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $studioLogs 'latest-server-log.json')
    $studioDeadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $studioDeadline) {
        Start-Sleep -Milliseconds 250
        $studioProcess.Refresh()
        if ($studioProcess.HasExited) { throw "Studio exited with code $($studioProcess.ExitCode). See $studioErrorLog. Another process may already be using port 8766." }
        try {
            $studioHealth = Invoke-RestMethod 'http://127.0.0.1:8766/api/bootstrap' -TimeoutSec 1
            if (Test-StudioResponse $studioHealth) { $studioOnline = $true; break }
        } catch {}
    }
    if (-not $studioOnline) { throw "Studio did not become ready within 20 seconds. See $studioErrorLog. The server was not forcibly stopped." }
}
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8766' }
Write-Host 'H3 Prompt Studio is ready at http://127.0.0.1:8766'

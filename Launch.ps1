param([switch]$NoBrowser, [switch]$SkipLMStudio)

$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$studioPython = Join-Path $studioRoot '.venv\Scripts\python.exe'
$studioLogs = Join-Path $studioRoot 'logs'
# Every normal local launch uses the user's requested family lock. The backend
# also enforces the durable settings policy when started directly.
$env:H3_STUDIO_MODEL_POLICY = 'qwen3.8-27b'
New-Item -ItemType Directory -Path $studioLogs -Force | Out-Null
if (-not (Test-Path -LiteralPath $studioPython -PathType Leaf)) { throw 'Run Setup.ps1 first to install the local runtime.' }
if (-not (Test-Path -LiteralPath (Join-Path $studioRoot 'dist\index.html') -PathType Leaf)) { throw 'Run Setup.ps1 first to build the editor.' }

function Test-StudioResponse {
    param($Health)
    return ($null -ne $Health -and $Health.version -match '^\d+\.\d+\.\d+$' -and
            $Health.token -is [string] -and $Health.token.Length -ge 32 -and
            $null -ne $Health.project -and $Health.project.schema_version -eq 1 -and
            $Health.project.id -is [string])
}

function Get-StudioFingerprint {
    param([string]$Root)
    [string[]]$studioCodeNames = @(Get-ChildItem -LiteralPath (Join-Path $Root 'backend') -Filter '*.py' -File | Select-Object -ExpandProperty Name)
    if (-not $studioCodeNames.Count) { throw 'The local Studio backend source folder is unavailable.' }
    [Array]::Sort($studioCodeNames, [StringComparer]::Ordinal)
    $studioManifest = [Collections.Generic.List[string]]::new()
    foreach ($studioCodeName in $studioCodeNames) {
        $studioCodeHash = (Get-FileHash -LiteralPath (Join-Path (Join-Path $Root 'backend') $studioCodeName) -Algorithm SHA256).Hash.ToLowerInvariant()
        $studioManifest.Add("backend/$studioCodeName=$studioCodeHash")
    }
    $studioHash = [Security.Cryptography.SHA256]::Create()
    try {
        $studioManifestBytes = [Text.Encoding]::UTF8.GetBytes([string]::Join("`n", $studioManifest))
        return [BitConverter]::ToString($studioHash.ComputeHash($studioManifestBytes)).Replace('-', '').ToLowerInvariant()
    } finally { $studioHash.Dispose() }
}

function Assert-StudioIdentity {
    param($Health, [string]$Root, [string]$Version, [string]$Fingerprint)
    if (-not (Test-StudioResponse $Health)) { throw 'Port 8766 answered, but it did not identify itself as H3 Prompt Studio.' }
    if ($Health.workspace_root -ne $Root) { throw "Port 8766 is serving another or older Studio copy. Close that server before launching this folder: $Root." }
    if ($Health.version -ne $Version) { throw "Port 8766 is running Studio $($Health.version), but this folder is version $Version. Restart that server with this Launch.ps1 to apply the update." }
    if ($Health.code_fingerprint -ne $Fingerprint) { throw 'Port 8766 is running older Python code than this folder. Restart that server with this Launch.ps1 to apply the update; it was not stopped automatically.' }
    if ($Health.settings.assistant_model_policy -ne 'qwen3.8-27b') { throw 'Port 8766 is serving a Studio instance without the Qwen 3.8 27B lock. Restart that server with this Launch.ps1 to apply the update.' }
}

$studioExpectedVersion = (Get-Content -LiteralPath (Join-Path $studioRoot 'frontend\package.json') -Raw | ConvertFrom-Json).version
$studioExpectedFingerprint = Get-StudioFingerprint $studioRoot

function Test-LMStudioResponse {
    param([string]$Origin)
    try {
        $studioLmHealth = Invoke-RestMethod "$Origin/api/v1/models" -TimeoutSec 2
        return ($null -ne $studioLmHealth -and $studioLmHealth.models -is [array])
    } catch {
        # An authenticated running server must not be restarted as a second copy.
        return ($null -ne $_.Exception.Response -and [int]$_.Exception.Response.StatusCode -in @(401, 403))
    }
}

function Get-StudioLMOrigin {
    param([string]$SettingsPath)
    $studioLmOrigin = 'http://127.0.0.1:1234'
    if (Test-Path -LiteralPath $SettingsPath -PathType Leaf) {
        $studioSavedSettings = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
        if ($studioSavedSettings.lm_url) {
            $studioLmUri = [Uri]$studioSavedSettings.lm_url
            if ($studioLmUri.Scheme -ne 'http' -or $studioLmUri.Host -notin @('127.0.0.1', 'localhost', '::1', '[::1]') -or $studioLmUri.UserInfo) {
                throw 'LM Studio must use a local HTTP address. Correct data/settings.json before launching.'
            }
            $studioLmOrigin = $studioLmUri.GetLeftPart([UriPartial]::Authority)
        }
    }
    return $studioLmOrigin
}

if (-not $SkipLMStudio) {
    $studioDataPath = if ($env:H3_STUDIO_DATA) { $env:H3_STUDIO_DATA } else { Join-Path $studioRoot 'data' }
    $studioLmOrigin = Get-StudioLMOrigin (Join-Path $studioDataPath 'settings.json')
    if (-not (Test-LMStudioResponse $studioLmOrigin)) {
        $studioLmsCommand = Get-Command lms.exe -CommandType Application -ErrorAction SilentlyContinue
        $studioLmsPath = if ($studioLmsCommand) { $studioLmsCommand.Source } else { Join-Path $env:USERPROFILE '.lmstudio\bin\lms.exe' }
        if (Test-Path -LiteralPath $studioLmsPath -PathType Leaf) {
            $studioLmPort = ([Uri]$studioLmOrigin).Port
            $studioLmBind = if (([Uri]$studioLmOrigin).Host -in @('::1', '[::1]')) { '::1' } else { '127.0.0.1' }
            $studioLmStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
            $studioLmOut = Join-Path $studioLogs "lm-server-$studioLmStamp.out.log"
            $studioLmError = Join-Path $studioLogs "lm-server-$studioLmStamp.err.log"
            $studioLmProcess = Start-Process -FilePath $studioLmsPath -ArgumentList @('server', 'start', '--port', "$studioLmPort", '--bind', $studioLmBind) -WorkingDirectory $studioRoot -WindowStyle Hidden -RedirectStandardOutput $studioLmOut -RedirectStandardError $studioLmError -PassThru
            $studioLmDeadline = (Get-Date).AddSeconds(8)
            while ((Get-Date) -lt $studioLmDeadline -and -not (Test-LMStudioResponse $studioLmOrigin)) {
                Start-Sleep -Milliseconds 300
                $studioLmProcess.Refresh()
                if ($studioLmProcess.HasExited -and $studioLmProcess.ExitCode -ne 0) { break }
            }
            if (-not (Test-LMStudioResponse $studioLmOrigin)) { Write-Warning "LM Studio is not ready. Start its local server, then refresh Connections. See $studioLmError. Local editing and playback remain available." }
        } else {
            Write-Warning 'LM Studio CLI was not found. Start its local server in LM Studio, then refresh Connections. Local editing and playback remain available.'
        }
    }
}

$studioOnline = $false
try {
    $studioHealth = Invoke-RestMethod 'http://127.0.0.1:8766/api/bootstrap' -TimeoutSec 2
    Assert-StudioIdentity $studioHealth $studioRoot $studioExpectedVersion $studioExpectedFingerprint
    $studioOnline = $true
} catch {
    if ($_.Exception.Message -like 'Port 8766 *') { throw }
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
            if (Test-StudioResponse $studioHealth) {
                Assert-StudioIdentity $studioHealth $studioRoot $studioExpectedVersion $studioExpectedFingerprint
                $studioOnline = $true; break
            }
        } catch { if ($_.Exception.Message -like 'Port 8766 *') { throw } }
    }
    if (-not $studioOnline) { throw "Studio did not become ready within 20 seconds. See $studioErrorLog. The server was not forcibly stopped." }
}
if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8766' }
Write-Host 'H3 Prompt Studio is ready at http://127.0.0.1:8766'

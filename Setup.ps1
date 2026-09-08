$ErrorActionPreference = 'Stop'
$studioRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

function Invoke-StudioNative {
    param([string]$CommandPath, [string[]]$CommandArguments)
    & $CommandPath @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Setup stopped: $([IO.Path]::GetFileName($CommandPath)) exited with code $LASTEXITCODE. Fix the error above and run Setup.ps1 again."
    }
}

$studioUv = (Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
$studioNpm = (Get-Command npm.cmd -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
$studioNode = (Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
if (-not $studioUv) { throw 'Install uv for Windows, then open a fresh terminal: https://docs.astral.sh/uv/getting-started/installation/' }
if (-not $studioNpm -or -not $studioNode) { throw 'Install Node.js 22.12 or newer (including npm), then open a fresh terminal: https://nodejs.org/' }
foreach ($studioMediaTool in @('ffmpeg.exe', 'ffprobe.exe')) {
    if (-not (Get-Command $studioMediaTool -CommandType Application -ErrorAction SilentlyContinue)) {
        throw "Install FFmpeg and add both ffmpeg.exe and ffprobe.exe to PATH before setup. Missing: $studioMediaTool. https://ffmpeg.org/download.html"
    }
}
$studioNodeVersionText = & $studioNode -p 'process.versions.node'
if ($LASTEXITCODE -ne 0) { throw 'Node.js could not report its version.' }
$studioNodeVersion = [version]$studioNodeVersionText
if (-not (($studioNodeVersion.Major -eq 20 -and $studioNodeVersion -ge [version]'20.19') -or $studioNodeVersion -ge [version]'22.12')) {
    throw "Node.js $studioNodeVersion is unsupported by this frontend. Use Node.js 22.12 or newer."
}
$studioPython = Join-Path $studioRoot '.venv\Scripts\python.exe'
$studioRequirements = if (Test-Path -LiteralPath (Join-Path $studioRoot 'requirements.lock.txt') -PathType Leaf) { 'requirements.lock.txt' } else { 'requirements.txt' }
Push-Location $studioRoot
try {
    # Reuse a working app environment; do not recreate it on a setup retry.
    if (-not (Test-Path -LiteralPath $studioPython -PathType Leaf)) {
        Invoke-StudioNative $studioUv @('venv', '.venv', '--python', '3.12')
    }
    Invoke-StudioNative $studioUv @('pip', 'install', '--python', $studioPython, '-r', $studioRequirements)
    Push-Location frontend
    try {
        Invoke-StudioNative $studioNpm @('ci', '--no-fund', '--no-audit')
        Invoke-StudioNative $studioNpm @('run', 'build')
    } finally { Pop-Location }
    if (-not (Test-Path -LiteralPath (Join-Path $studioRoot 'dist\index.html') -PathType Leaf)) {
        throw 'The frontend build did not produce dist/index.html.'
    }
} finally { Pop-Location }
Write-Host 'Prompt Studio is ready. Run Launch.ps1 to open it. Existing project data was retained.'

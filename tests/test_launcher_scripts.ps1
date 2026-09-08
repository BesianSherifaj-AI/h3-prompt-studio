# CPU-only checks: parse source and invoke only its pure/checking helper functions.
# This never executes Setup.ps1 or Launch.ps1, installs packages, or starts a server.
$ErrorActionPreference = 'Stop'
$studioTestRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
foreach ($studioScript in @('Setup.ps1', 'Launch.ps1')) {
    $studioTokens = $null
    $studioErrors = $null
    $studioAst = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $studioTestRoot $studioScript), [ref]$studioTokens, [ref]$studioErrors)
    if ($studioErrors.Count) { throw ($studioErrors | Out-String) }
    foreach ($studioFunction in $studioAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)) {
        . ([scriptblock]::Create($studioFunction.Extent.Text))
    }
}
$studioTestPython = Join-Path $studioTestRoot '.venv\Scripts\python.exe'
$studioTestFile = New-TemporaryFile
try {
    # Windows PowerShell 5.1 strips embedded quotes in native -c arguments.
    # A local script tests forwarding without relying on shell code quoting.
    $studioTestSource = @'
import sys
if sys.argv[1:] == ['fail']:
    sys.exit(19)
assert sys.argv[1:] == ['space value', 'last'], repr(sys.argv)
'@
    [IO.File]::WriteAllText($studioTestFile.FullName, $studioTestSource, [Text.UTF8Encoding]::new($false))
    Invoke-StudioNative $studioTestPython @($studioTestFile.FullName, 'space value', 'last')
    $studioFailedAsExpected = $false
    try { Invoke-StudioNative $studioTestPython @($studioTestFile.FullName, 'fail') } catch { $studioFailedAsExpected = $_.Exception.Message -like '*exited with code 19*' }
    if (-not $studioFailedAsExpected) { throw 'Setup did not stop on a failed native command.' }
} finally {
    Remove-Item -LiteralPath $studioTestFile.FullName -Force
}
$studioValidHealth = [pscustomobject]@{ version = '1.0.0'; token = ('a' * 43); project = [pscustomobject]@{ schema_version = 1; id = 'project' } }
if (-not (Test-StudioResponse $studioValidHealth)) { throw 'Valid health response rejected.' }
foreach ($studioBadHealth in @($null, [pscustomobject]@{ version = '1.0.0' }, [pscustomobject]@{ version = 'other'; token = ('a' * 43); project = @{ schema_version = 1; id = 'project' } })) {
    if (Test-StudioResponse $studioBadHealth) { throw 'Unrelated service accepted as Studio.' }
}
Write-Output 'Launcher/setup syntax, argument forwarding, native exit failure gate and response identity checks passed. No server or model was started.'

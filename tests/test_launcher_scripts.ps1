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
$studioFingerprint = Get-StudioFingerprint $studioTestRoot
$studioIdentityHealth = [pscustomobject]@{ version = '1.7.0'; code_fingerprint = $studioFingerprint; workspace_root = $studioTestRoot; token = ('a' * 43); project = [pscustomobject]@{ schema_version = 1; id = 'project' }; settings = [pscustomobject]@{ assistant_model_policy = 'qwen3.8-27b' } }
Assert-StudioIdentity $studioIdentityHealth $studioTestRoot '1.7.0' $studioFingerprint
foreach ($studioIdentityChange in @(@('version', '1.6.1'), @('code_fingerprint', ('0' * 64)), @('code_fingerprint', $null), @('workspace_root', 'C:\other-studio'))) {
    $studioWrongIdentity = $studioIdentityHealth | ConvertTo-Json -Depth 4 | ConvertFrom-Json
    $studioWrongIdentity.($studioIdentityChange[0]) = $studioIdentityChange[1]
    $studioRejectedIdentity = $false
    try { Assert-StudioIdentity $studioWrongIdentity $studioTestRoot '1.7.0' $studioFingerprint } catch { $studioRejectedIdentity = $_.Exception.Message -like 'Port 8766 *' }
    if (-not $studioRejectedIdentity) { throw "Launcher accepted an outdated or unrelated server: $($studioIdentityChange[0])." }
}
$studioFingerprintScript = New-TemporaryFile
try {
    $studioFingerprintSource = @'
import sys
sys.path.insert(0, sys.argv[1])
from backend.runtime_identity import runtime_fingerprint
print(runtime_fingerprint(sys.argv[1]))
'@
    [IO.File]::WriteAllText($studioFingerprintScript.FullName, $studioFingerprintSource, [Text.UTF8Encoding]::new($false))
    $studioPythonFingerprint = Invoke-StudioNative $studioTestPython @($studioFingerprintScript.FullName, $studioTestRoot)
    if ($studioPythonFingerprint.Trim() -ne $studioFingerprint) { throw 'Python and launcher source fingerprints disagree.' }
} finally { Remove-Item -LiteralPath $studioFingerprintScript.FullName -Force }
$studioTestSettings = New-TemporaryFile
try {
    [IO.File]::WriteAllText($studioTestSettings.FullName, '{"lm_url":"http://localhost:4321/v1"}', [Text.UTF8Encoding]::new($false))
    if ((Get-StudioLMOrigin $studioTestSettings.FullName) -ne 'http://localhost:4321') { throw 'Launcher did not preserve the configured local LM Studio port.' }
    [IO.File]::WriteAllText($studioTestSettings.FullName, '{"lm_url":"http://example.com:1234/v1"}', [Text.UTF8Encoding]::new($false))
    $studioRejectedRemote = $false
    try { Get-StudioLMOrigin $studioTestSettings.FullName } catch { $studioRejectedRemote = $_.Exception.Message -like '*local HTTP*' }
    if (-not $studioRejectedRemote) { throw 'Launcher accepted a remote LM Studio URL.' }
} finally { Remove-Item -LiteralPath $studioTestSettings.FullName -Force }
Write-Output 'Launcher/setup syntax, argument forwarding, native exit failure gate, version/source identity, cross-language fingerprint and local LM configuration checks passed. No server or model was started.'

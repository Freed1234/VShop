[CmdletBinding()]
param([string]$TestDir = (Join-Path $env:TEMP ('VShop-portable-' + [guid]::NewGuid().ToString('N'))))
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Archive = Join-Path $PSScriptRoot 'release\VShop-main-Windows11-x64.zip'
if (Test-Path -LiteralPath $TestDir) { throw 'Choose a new test directory; existing files will not be overwritten.' }
New-Item -ItemType Directory -Path $TestDir | Out-Null
$TestDir = (Resolve-Path -LiteralPath $TestDir).Path
Expand-Archive -LiteralPath $Archive -DestinationPath $TestDir
$Executable = Join-Path $TestDir 'VShopPersonal\VShopPersonal.exe'
$Saved = @{}
foreach ($Name in @('PATH', 'PYTHONHOME', 'PYTHONPATH', 'LOCALAPPDATA', 'QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QTWEBENGINEPROCESS_PATH', 'QTWEBENGINE_RESOURCES_PATH', 'QTWEBENGINE_LOCALES_PATH', 'QTWEBENGINE_CHROMIUM_FLAGS', 'QT_OPENGL', 'QT_QUICK_BACKEND')) {
    $Saved[$Name] = [Environment]::GetEnvironmentVariable($Name, 'Process')
}
try {
    foreach ($Name in $Saved.Keys) { [Environment]::SetEnvironmentVariable($Name, $null, 'Process') }
    $env:PATH = Join-Path $env:SystemRoot 'System32'
    $env:LOCALAPPDATA = $TestDir
    foreach ($Mode in @('normal', 'software')) {
        $Report = Join-Path $TestDir ($Mode + '.json')
        $Arguments = '--self-test "' + $Report + '"'
        if ($Mode -eq 'software') { $Arguments = '--software-rendering ' + $Arguments }
        $Process = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $TestDir -WindowStyle Hidden -PassThru
        if (-not $Process.WaitForExit(45000)) {
            $Process.Kill()
            throw "Packaged $Mode test timed out."
        }
        $Process.Refresh()
        if ($Process.ExitCode -ne 0) { throw "Packaged $Mode test exited with $($Process.ExitCode)." }
        $Result = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
        if (-not $Result.ok -or -not $Result.frozen) { throw "Packaged $Mode test failed." }
        Write-Host "$Mode test passed: $Report"
    }
} finally {
    foreach ($Name in $Saved.Keys) { [Environment]::SetEnvironmentVariable($Name, $Saved[$Name], 'Process') }
}
Write-Host 'Offline UI and WebEngine tests passed. Riot login and a separate clean Windows PC still require manual verification.'

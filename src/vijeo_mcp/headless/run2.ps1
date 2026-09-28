param([string]$SwxCF, [string]$PanelPath, [string]$Mode = "init,save")
$ErrorActionPreference = "Stop"
if ([IntPtr]::Size -ne 4) { throw "must run in 32-bit PowerShell" }
$vijeo = "C:\Program Files (x86)\Schneider Electric\Vijeo-Designer 6.2\Vijeo-Frame"
$env:PATH = "$vijeo;$env:PATH"
[System.IO.Directory]::SetCurrentDirectory($vijeo)
Add-Type -Path (Join-Path $PSScriptRoot "VijeoHeadless2.cs")
try { [VijeoHeadless2.Runner]::Run($SwxCF, $PanelPath, $Mode) }
catch { "EXCEPTION: " + $_.Exception.GetBaseException().Message }

# Runs in 32-bit PowerShell (C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe).
# Usage: roundtrip.ps1 <swxcf> <panel storage path> <out file> <streams csv>
param([string]$SwxCF, [string]$PanelPath, [string]$OutFile, [string]$Streams = "GraphicalObject,StatusFlags,WindowObject")
$ErrorActionPreference = "Stop"
if ([IntPtr]::Size -ne 4) { throw "must run in 32-bit PowerShell" }
$vijeo = "C:\Program Files (x86)\Schneider Electric\Vijeo-Designer 6.2\Vijeo-Frame"
$env:PATH = "$vijeo;$env:PATH"
[System.IO.Directory]::SetCurrentDirectory($vijeo)
Add-Type -Path (Join-Path $PSScriptRoot "VijeoHeadless.cs")
try {
    [VijeoHeadless.Runner]::RoundTrip($SwxCF, $PanelPath, $OutFile, $Streams)
} catch {
    "EXCEPTION: " + $_.Exception.GetBaseException().Message
    "  at: " + $_.Exception.GetBaseException().GetType().FullName
}

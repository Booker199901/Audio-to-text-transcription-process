param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $RemainingArguments
)

$ErrorActionPreference = "Stop"

# Keep this launcher ASCII-only so Windows PowerShell 5.1 will not misread UTF-8 without a BOM.
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONUTF8 = "1"

# Always use the project environment rather than an unrelated global Python installation.
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "Not installed. Run: powershell -ExecutionPolicy Bypass -File .\setup.ps1"
}

# Add project-local NVIDIA runtime directories when the optional GPU packages are installed.
$NvidiaRoot = Join-Path $PSScriptRoot ".venv\Lib\site-packages\nvidia"
$NvidiaDllDirectories = @(
    (Join-Path $NvidiaRoot "cublas\bin"),
    (Join-Path $NvidiaRoot "cudnn\bin"),
    (Join-Path $NvidiaRoot "cuda_nvrtc\bin")
) | Where-Object { Test-Path -LiteralPath $_ }
if ($NvidiaDllDirectories.Count -gt 0) {
    $env:PATH = ($NvidiaDllDirectories -join [System.IO.Path]::PathSeparator) + [System.IO.Path]::PathSeparator + $env:PATH
}

# Forward options such as --overwrite and explicit recording paths to the Python CLI.
& ".venv\Scripts\python.exe" "transcribe.py" @RemainingArguments
exit $LASTEXITCODE

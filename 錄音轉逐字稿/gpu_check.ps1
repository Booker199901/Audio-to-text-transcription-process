param(
    [switch] $LoadModel
)

$ErrorActionPreference = "Stop"

# Keep this launcher ASCII-only so Windows PowerShell 5.1 parses it consistently.
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONUTF8 = "1"

# Require the project-local interpreter so the CTranslate2 version is deterministic.
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    throw "Project environment not found. Run: powershell -ExecutionPolicy Bypass -File .\setup.ps1"
}

# Add project-local CUDA 12 and cuDNN 9 DLL directories before testing CTranslate2.
$NvidiaRoot = Join-Path $PSScriptRoot ".venv\Lib\site-packages\nvidia"
$NvidiaDllDirectories = @(
    (Join-Path $NvidiaRoot "cublas\bin"),
    (Join-Path $NvidiaRoot "cudnn\bin"),
    (Join-Path $NvidiaRoot "cuda_nvrtc\bin")
) | Where-Object { Test-Path -LiteralPath $_ }
if ($NvidiaDllDirectories.Count -gt 0) {
    $env:PATH = ($NvidiaDllDirectories -join [System.IO.Path]::PathSeparator) + [System.IO.Path]::PathSeparator + $env:PATH
}

# Check whether the NVIDIA driver command is available before probing CUDA from Python.
$nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($null -eq $nvidiaSmi) {
    Write-Host "nvidia-smi was not found. Install an NVIDIA driver or use CPU mode."
    exit 2
}
& $nvidiaSmi.Source
if ($LASTEXITCODE -ne 0) {
    throw "nvidia-smi failed. Repair the NVIDIA driver before enabling GPU mode."
}

# Ask CTranslate2 how many CUDA devices are visible to this project environment.
# Keep the inline Python free of quoted strings because Windows PowerShell can strip embedded quotes.
$probe = "import ctranslate2; count=ctranslate2.get_cuda_device_count(); print(ctranslate2.__version__); print(count); raise SystemExit(0 if count > 0 else 1)"
$probeOutput = & ".venv\Scripts\python.exe" -c $probe
$probeOutput | ForEach-Object { Write-Host $_ }
if ($LASTEXITCODE -ne 0) {
    Write-Error "No CUDA device found. Check CUDA 12/cuDNN 9 DLLs on PATH."
    exit $LASTEXITCODE
}

# Optionally load a small model to exercise the complete CUDA runtime path.
if ($LoadModel) {
    & ".venv\Scripts\python.exe" ".\gpu_smoke_test.py"
    exit $LASTEXITCODE
}

Write-Host "GPU check passed. Run: powershell -ExecutionPolicy Bypass -File .\run.ps1 --device cuda --compute-type float16"

$ErrorActionPreference = "Stop"

# Keep this launcher ASCII-only so Windows PowerShell 5.1 will not misread UTF-8 without a BOM.
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONUTF8 = "1"

# Prefer Python 3.11 from the Windows launcher, then accept a supported global Python.
$PythonCommand = Get-Command py -ErrorAction SilentlyContinue
if ($null -ne $PythonCommand) {
    & py -3.11 -c "import sys; print(sys.version)" | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $BasePython = @("py", "-3.11")
    }
}

if ($null -eq $BasePython) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $PythonCommand) {
        throw "Python was not found. Install 64-bit Python 3.11, 3.12, or 3.13 and run this script again."
    }
    & python -c "import sys; raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 14) else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "Unsupported Python version. Install 64-bit Python 3.11, 3.12, or 3.13."
    }
    $BasePython = @("python")
}

# Create a project-local environment so this installation does not change other Python projects.
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    if ($BasePython.Count -eq 2) {
        & $BasePython[0] $BasePython[1] -m venv .venv
    }
    else {
        & $BasePython[0] -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Python virtual environment."
    }
}

# Install runtime packages. Internet is needed here, but transcription can be local after model download.
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip."
}
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install requirements.txt."
}

# Create the two folders used by non-technical users.
New-Item -ItemType Directory -Force -Path "input" | Out-Null
New-Item -ItemType Directory -Force -Path "output" | Out-Null

Write-Host "Setup complete. Put recordings in input, then run .\run.ps1"
Write-Host "The first run downloads large-v3. To prefetch it: .\run.ps1 --download-model-only"

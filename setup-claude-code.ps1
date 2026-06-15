# ============================================================
#  Claude Code environment checker
#  Usage: right-click -> Run with PowerShell
# ============================================================

$host.UI.RawUI.WindowTitle = "Claude Code RP Setup"

function Write-Step($msg) { Write-Host "`n[>] $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  !!  $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  XX  $msg" -ForegroundColor Red }
function Write-Banner($msg) {
    $line = "=" * 60
    Write-Host "`n$line" -ForegroundColor Magenta
    Write-Host "  $msg" -ForegroundColor Magenta
    Write-Host "$line" -ForegroundColor Magenta
}

function Update-SessionPath {
    foreach ($scope in @("Machine", "User")) {
        $envPath = [Environment]::GetEnvironmentVariable("Path", $scope)
        if ($envPath) {
            foreach ($entry in $envPath -split ";") {
                $trimmed = $entry.Trim()
                if ($trimmed -and (Test-Path $trimmed)) {
                    $existing = [Environment]::GetEnvironmentVariable("Path", "Process") -split ";"
                    if ($trimmed -notin $existing) {
                        [Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "Process") + ";$trimmed", "Process")
                    }
                }
            }
        }
    }
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Process")
}

function Test-Winget {
    try { $null = Get-Command winget -ErrorAction Stop; return $true } catch { return $false }
}

function Install-NodeJS {
    Write-Host "  Installing Node.js LTS..." -ForegroundColor Yellow
    if (Test-Winget) {
        winget install OpenJS.NodeJS.LTS --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
        Update-SessionPath
        try { $v = node --version 2>&1; Write-OK "Node.js installed: $v"; return } catch {}
    }
    Write-Err "Cannot auto-install Node.js. Please install manually: https://nodejs.org/"
    exit 1
}

function Install-Git {
    Write-Host "  Installing Git for Windows..." -ForegroundColor Yellow
    if (Test-Winget) {
        winget install Git.Git --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
        Update-SessionPath
        try { $v = git --version 2>&1; Write-OK "Git installed: $v"; return } catch {}
    }
    Write-Err "Cannot auto-install Git. Please install manually: https://git-scm.com/download/win"
    exit 1
}

Clear-Host
Write-Banner "Claude Code RP Environment Checker"
Write-Host ""
Write-Host "This script will:" -ForegroundColor White
Write-Host "  1. Check/install Node.js and Git"
Write-Host "  2. Check/install/update Claude Code"
Write-Host "  3. Leave your existing Claude Code model/API configuration untouched"
Write-Host ""

Write-Step "Checking runtime dependencies..."
try {
    $nodeVer = node --version 2>&1
    if ($LASTEXITCODE -eq 0) { Write-OK "Node.js found: $nodeVer" } else { Install-NodeJS }
} catch { Install-NodeJS }

try {
    $gitVer = git --version 2>&1
    if ($LASTEXITCODE -eq 0) { Write-OK "Git found: $gitVer" } else { Install-Git }
} catch { Install-Git }

Write-Step "Checking Claude Code..."
$needInstall = $false
try {
    $claudeVer = claude --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "Claude Code found: $claudeVer"
        $choice = Read-Host "  Update Claude Code now? (Y/N, default N)"
        if ($choice -eq "Y" -or $choice -eq "y") { $needInstall = $true }
    } else { $needInstall = $true }
} catch { $needInstall = $true }

if ($needInstall) {
    Write-Host "  Installing/updating Claude Code..." -ForegroundColor Yellow
    $env:CI = "true"
    $env:npm_config_yes = "true"
    npm install -g @anthropic-ai/claude-code 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Claude Code install failed. Manual command: npm install -g @anthropic-ai/claude-code"
        exit 1
    }
    $claudeVer = claude --version 2>&1
    Write-OK "Claude Code installed: $claudeVer"
}

Write-Step "Verifying Claude Code command..."
try {
    $claudeVer = claude --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "Claude Code available: $claudeVer"
    } else {
        Write-Err "Claude Code command failed"
        exit 1
    }
} catch {
    Write-Err "Claude Code command failed: $_"
    exit 1
}

Write-Banner "Environment check complete"
Write-Host ""
Write-Host "Usage:" -ForegroundColor White
Write-Host "  1. Open a new PowerShell terminal"
Write-Host "  2. Enter a card/save folder"
Write-Host "  3. Run: claude" -ForegroundColor Cyan
Write-Host "  4. In Claude Code, run: /rp" -ForegroundColor Cyan
Write-Host ""
Write-Host "This project does not override your Claude Code model/API configuration." -ForegroundColor DarkGray
Write-Host ""
Write-Host "Press any key to exit..." -ForegroundColor Gray
$null = $host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    & $Python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Offline tests failed.' }
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name WeChatWindowAssistant --distpath dist --workpath build/pyinstaller `
        --specpath build wechat_mode.py
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
    Write-Output 'Built dist/WeChatWindowAssistant.exe'
} finally {
    Pop-Location
}

# Compilar no Windows 10/11 x64. Produz um ZIP portatil com Python e aria2c embutidos.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (Get-Process -Name 'GetComics' -ErrorAction SilentlyContinue) {
    throw 'Fecha primeiro o GetComics.exe (inclusive no Gestor de Tarefas) e volta a compilar.'
}
$python = 'py'
& $python -3.12 -m venv .venv-build
if ($LASTEXITCODE -ne 0) { throw 'Precisas do Python 3.12 x64 para compilar.' }
$pip = Join-Path $PSScriptRoot '.venv-build\Scripts\python.exe'
& $pip -m pip install --upgrade pip
& $pip -m pip install -r requirements-windows.txt
if ($LASTEXITCODE -ne 0) { throw 'Falha na instalacao das dependencias.' }

# aria2c oficial v1.37.0: hash SHA256 confirmado no manifesto Scoop.
$version = '1.37.0'
$zipFile = Join-Path $env:TEMP 'getcomics-aria2-1.37.0.zip'
$release = 'https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip'
$expected = '67D015301EEF0B612191212D564C5BB0A14B5B9C4796B76454276A4D28D9B288'
Invoke-WebRequest -Uri $release -OutFile $zipFile
$actual = (Get-FileHash $zipFile -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw "Hash SHA256 invalido para aria2c: $actual" }
$extracted = Join-Path $env:TEMP 'getcomics-aria2-extract'
if (Test-Path $extracted) { Remove-Item $extracted -Recurse -Force }
Expand-Archive -Path $zipFile -DestinationPath $extracted -Force
$aria = Get-ChildItem $extracted -Filter 'aria2c.exe' -Recurse | Select-Object -First 1
if (-not $aria) { throw 'aria2c.exe nao encontrado dentro do ZIP oficial.' }
New-Item -ItemType Directory -Force -Path 'bin' | Out-Null
Copy-Item $aria.FullName 'bin\aria2c.exe' -Force

& $pip -m unittest discover -s tests -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) { throw 'Testes falharam; compilacao interrompida.' }
& $pip -m PyInstaller --noconfirm --clean --onedir --windowed --name GetComics `
    --paths . --add-data 'getcomics_web_ui\index.html;getcomics_web_ui' `
    --add-binary 'bin\aria2c.exe;.' `
    --collect-all webview --collect-all rich --collect-all certifi `
    --hidden-import=psutil --hidden-import=webview --hidden-import=tkinter --hidden-import=secretstash `
    launcher_windows.py
if ($LASTEXITCODE -ne 0) { throw 'Falhou a compilacao PyInstaller.' }
if (-not (Test-Path 'dist\GetComics\GetComics.exe')) { throw 'Falta o executavel final.' }
$smoke = Start-Process -FilePath (Resolve-Path 'dist\GetComics\GetComics.exe') -ArgumentList '--self-test' -Wait -PassThru
if ($smoke.ExitCode -ne 0) { throw 'Auto-teste do EXE falhou. Consulta startup.log em %APPDATA%\GetComics Local.' }
Copy-Item 'AVISOS-TERCEIROS.txt' 'dist\GetComics\AVISOS-TERCEIROS.txt' -Force
$archive = 'dist\GetComics-Windows-Portable.zip'
if (Test-Path $archive) { Remove-Item $archive -Force }
# O Defender/OneDrive pode manter o EXE bloqueado por alguns segundos depois do auto-teste.
$packed = $false
for ($attempt = 1; $attempt -le 4; $attempt++) {
    try {
        Start-Sleep -Seconds 3
        Compress-Archive -Path 'dist\GetComics' -DestinationPath $archive -Force -ErrorAction Stop
        $packed = $true
        break
    } catch {
        if (Test-Path $archive) { Remove-Item $archive -Force -ErrorAction SilentlyContinue }
        if ($attempt -eq 4) {
            throw ('O executavel compilou, mas nao foi possivel criar o ZIP. Fecha o GetComics e tenta compactar dist\GetComics manualmente fora do OneDrive. Erro: ' + $_.Exception.Message)
        }
    }
}
if (-not $packed) { throw 'Nao foi possivel criar o ZIP.' }
Write-Host "FICHEIRO FINAL: $archive" -ForegroundColor Green
Write-Host 'Envia o ZIP aos teus amigos; nao inclui credenciais nem definicoes pessoais.'

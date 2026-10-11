# Baut ArchivImporter.exe lokal unter Windows (PowerShell im Ordner windows-client):
#   .\build.ps1 -Version 0.1.0
param([string]$Version = "0.1.0-lokal")

$ErrorActionPreference = "Stop"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller
"__version__ = `"$Version`"" | Out-File -Encoding utf8 archiv_importer\_version.py

pyinstaller --noconfirm --clean --onefile --windowed `
  --name ArchivImporter `
  --paths ..\begleitdienst `
  --add-data "archiv_importer\static;archiv_importer\static" `
  --collect-submodules uvicorn --collect-all pypdfium2 --collect-all pypdfium2_raw `
  --collect-submodules archiv_core `
  --hidden-import archiv_core `
  start.py

Write-Host "Fertig: dist\ArchivImporter.exe"

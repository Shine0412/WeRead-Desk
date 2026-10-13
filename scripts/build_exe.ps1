# Build WeReadDesk.exe into the project root (single file, windowed, with icon)
$root = Split-Path $PSScriptRoot
& "$root\.venv\Scripts\pyinstaller.exe" --noconfirm --clean --onefile --windowed --name WeReadDesk --distpath "$root" --workpath "$root\build" --specpath "$root\build" --icon "$root\assets\icon.ico" --add-data "$root\assets\icon.ico;assets" --add-data "$root\assets\icon.png;assets" --add-data "$root\src\settings.html;src" --hidden-import webview.platforms.winforms "$root\src\app.py"

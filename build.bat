@echo off
chcp 65001 >nul
echo === بناء DealHunter.exe ===
python -m pip install -r requirements.txt pyinstaller || goto :err
python -m unittest discover -s tests || goto :err
python -m PyInstaller --noconfirm --clean --onefile --windowed --name DealHunter --icon assets\icon.ico --add-data "assets\icon.ico;assets" --add-data "dealhunter\web;dealhunter\web" --collect-all arabic_reshaper --collect-submodules pystray main.py || goto :err
echo.
echo تمام! الملف هنا: dist\DealHunter.exe
pause
exit /b 0
:err
echo حصل خطأ، راجع الرسائل اللي فوق.
pause
exit /b 1

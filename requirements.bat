@echo off
echo Installing required Python packages...
echo.

pip install numpy
pip install opencv-python
pip install mss
pip install PyAutoGUI
pip install keyboard
pip install pywin32

echo.
echo Installation complete. You can now run your Python script.
pause

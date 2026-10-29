@echo off
rem Starts Monday as the native desktop app, the same way the "Monday" shortcut does
rem (Desktop and Start menu): pythonw -m core.desktop, a tray app that runs Monday Core
rem hidden and supervised and opens Monday's own window. No console window, no browser HUD.
rem Launching it again while it's running just brings the window back (one instance).
rem Quit Monday from the tray: right-click the Monday icon near the clock and choose Quit.
rem Monday restarts Core itself if it stops, and to load a change (exit code 75); the tray
rem keeps the log at data\logs\core.log.
cd /d "%~dp0"
set "PYW=pythonw"
where pythonw >nul 2>&1 || set "PYW=C:\Program Files\Python314\pythonw.exe"
start "" "%PYW%" -m core.desktop

@echo off
rem The karaoke screen (docs/KARAOKE.md): the display page in its own Chrome, full screen, allowed to play sound
rem without anyone touching it. Its own profile (--user-data-dir) makes it a separate Chrome, so the sound flag applies
rem even while a normal Chrome is open (a flag given to an already-running Chrome is ignored).
rem Windows line endings and one command line: cmd silently drops a command split with ^ in an LF file.
rem Put the window on the TV (Windows display mode: Extend). Close it with Alt+F4.
start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" --user-data-dir="%LOCALAPPDATA%\screen-machine\karaoke-chrome" --autoplay-policy=no-user-gesture-required --no-first-run --no-default-browser-check --start-fullscreen --app=http://localhost:5000/display/vj

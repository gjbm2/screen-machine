# Windows screensaver

A real Windows screensaver (`.scr`) that shows one of the app's screens on an idle PC. It loads the TV's own page,
`/display/<screen>?kiosk=viewer&nomask&nocolour`, so it shows what that screen shows, live included. It loads it as a
viewer, so it never drives the screen, and at full brightness, without the room's light mask. Windows' own
screensaver logic decides when it starts and whether to lock on resume; this program only draws the page and exits on
input.

- **Get it:** in the app's menu, under Screens, each screen has a **Windows screensaver** chip (on computers only). It
  downloads `ScreenMachine-screensaver.exe`.
- **Install:** run the download. Unsigned, so SmartScreen asks first: "More info", then "Run anyway". Pick the
  screen, then press **Use as screensaver**. It copies itself to `%LOCALAPPDATA%\Programs\ScreenMachine\ScreenMachine.scr`,
  makes that the current user's screensaver (no admin needed), adds an entry under Apps for removal, and opens
  Windows' Screen saver settings, where you set the wait and the lock.
- **Change the screen:** use the **Settings...** button in Windows' Screen saver settings.
- **Screens for signed-in people only** (those whose `access` isn't `guest`): use **Sign in** in Settings. It opens
  the app on the screensaver's own browser profile, and the sign-in cookie lasts a year.
- **Remove:** use Apps, or **Remove** in Settings.

## How it works

- One C# file, `ScreenMachineSaver.cs`, built by Windows' own .NET Framework 4.8 compiler (C# 5).
- WebView2, which is part of Windows 11, draws the page. The SDK's two DLLs are embedded in the file and written to
  `%LOCALAPPDATA%\ScreenMachine\Screensaver\lib-*` on first run.
- **Start:** the window is DWM-cloaked until it has painted black, so the screen snaps straight to black. An injected
  script makes the page black and lays a black cover over it. The cover fades out over 1.5 s once the art is on
  screen, so the app's light background is never seen.
- **Exit:** low-level keyboard and mouse hooks end it on any key, click or wheel, or on a move of more than 12 px.
  A cursor-watching timer backs them up.
- **Preview:** the little monitor in Windows' dialog shows the screen's current thumbnail, without a browser.
- **What it shows:** each download carries its server (the page's origin) and screen as a trailer appended by
  `routes/screensaver.py`: `[JSON][int32 length]["SMSS"]`. Settings stores any change in `HKCU\Software\ScreenMachine`.
- **Log:** `%LOCALAPPDATA%\ScreenMachine\Screensaver\screensaver.log`.

## Build and check (from WSL on a Windows PC)

```
tools/screensaver/build.sh          # -> tools/screensaver/out/ScreenMachine-screensaver.exe (served by the route)
SM_SCREENSAVER_TEST_SERVER=https://<app address> SM_SCREENSAVER_TEST_SCREEN=<screen id> tools/screensaver/build.sh --test
```

`--test` runs `/selftest`, which changes nothing on the PC: no registry, no screensaver. It:

- reads the screen list;
- draws Settings and the preview to PNGs;
- loads the page off-screen on its own profile;
- logs the brightness of each frame from the start, which must stay black until the art is ready and then fade up.

Results go to `out/selftest/`.

## Limits

- **Unsigned:** SmartScreen warns on the download until the file is code-signed.
- **Live adds load:** while the screen is live, each running screensaver pulls the live stream like any viewer.
- **Displays may stay on:** Windows may keep a display on while it plays video, so with a clip or live on the page the
  monitor may not sleep at its usual time.
- **Managed PCs:** a group policy that fixes the screensaver overrides this one, and the installer says so.
- **Multiple monitors:** every monitor shows the same screen.

# Windows screensaver

A real Windows screensaver (`.scr`) that shows one of the app's screens on an idle PC. It loads the TV's own page,
`/display/<screen>?kiosk=viewer&nomask&nocolour`, so it shows what that screen shows, live included. It loads it as a
viewer, so it never drives the screen, and at full brightness, without the room's light mask. Windows' own
screensaver logic decides when it starts and whether to lock on resume; this program only draws the page and exits on
input.

- **Get it:** in the app's menu, under Screens, each screen has a **Windows screensaver** chip (on computers only). It
  downloads `ScreenMachine-screensaver.exe`.
- **Install:** run the download. Unsigned, so SmartScreen asks first: "More info", then "Run anyway". Pick the
  screen for each monitor, then press **Use as screensaver**. It copies itself to `%LOCALAPPDATA%\Programs\ScreenMachine\ScreenMachine.scr`,
  makes that the current user's screensaver (no admin needed), adds an entry under Apps for removal, and opens
  Windows' Screen saver settings, where you set the wait and the lock.
- **Choose per monitor:** use the **Settings...** button in Windows' Screen saver settings. It lists the monitors
  attached now, and for each one you choose:
  - a screen, or Nothing (black);
  - Fill (crops the edges) or Fit (black bars). Fit only applies to the art; live video is always fitted.

  A row for **monitors plugged in later** covers any monitor without a choice of its own. **Identify monitors**
  puts each row\'s number on its monitor.
- **Screens for signed-in people only** (those whose `access` isn't `guest`): use **Sign in** in Settings. It opens
  the app on the screensaver's own browser profile, and the sign-in cookie lasts a year.
- **Remove:** use Apps, or **Remove from this PC** in Settings.

## How it works

- One C# file, `ScreenMachineSaver.cs`, built by Windows' own .NET Framework 4.8 compiler (C# 5).
- WebView2, which is part of Windows 11, draws the page. The SDK's two DLLs are embedded in the file and written to
  `%LOCALAPPDATA%\ScreenMachine\Screensaver\lib-*` on first run.
- **Start:** the window is DWM-cloaked until it has painted black, so the screen snaps straight to black. An injected
  script makes the page black and lays a black cover over it. The cover fades out over 1.5 s once the art is on
  screen, so the app's light background is never seen.
- **Exit:** any key, click, tap or wheel, or a mouse move of more than 12 px, ends it at once, and the page never
  sees that input, so it can't open the display page's hover or tap controls. Two routes:
  - low-level keyboard and mouse hooks, backed up by a cursor-watching timer;
  - an injected script that stops every input event at the page's window and posts it to the saver. This is the
    only route for touch, which the browser takes directly and the mouse hook never sees.
- **Monitors:** one window and one page per monitor. Each monitor is known by its device path from
  QueryDisplayConfig, which is that physical monitor on that connector. A laptop's choice therefore comes back
  whenever the same monitor is plugged in again, whatever number Windows gives it that day.
  - The monitors are read each time the screensaver starts.
  - While it runs, a display change (dock, undock, lid) re-arranges after 1 s: windows move, new monitors get a
    page, and the windows of monitors that went away close.
  - Mirrored monitors share one window.
- **Preview:** the little monitor in Windows' dialog shows the main monitor's screen thumbnail, filled or fitted, without a browser.
- **What it shows:** each download carries its server (the page's origin) and screen as a trailer appended by
  `routes/screensaver.py`: `[JSON][int32 length]["SMSS"]`. Settings stores choices in `HKCU\Software\ScreenMachine\Screensaver`: the server, the default (`Screen`, `Fit`), and `Monitors\<id>` for each monitor.
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
- logs the brightness of each frame from the start, which must stay black until the art is ready and then fade up;
- sends a tap, which must reach the saver and not the page (`shield: ... OK`).

Results go to `out/selftest/`.

## Limits

- **Unsigned:** SmartScreen warns on the download until the file is code-signed.
- **Live adds load:** while the screen is live, each running screensaver pulls the live stream like any viewer.
- **Displays may stay on:** Windows may keep a display on while it plays video, so with a clip or live on the page the
  monitor may not sleep at its usual time.
- **Managed PCs:** a group policy that fixes the screensaver overrides this one, and the installer says so.

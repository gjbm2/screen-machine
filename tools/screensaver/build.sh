#!/usr/bin/env bash
# Builds the Windows screensaver (README.md) with the C# compiler that ships with Windows' .NET Framework 4.8: nothing
# to install. Run from WSL on a Windows machine. Output: tools/screensaver/out/ScreenMachine-screensaver.exe, which
# GET /api/screensaver/download serves (routes/screensaver.py). `build.sh --test` also runs its self-test (off-screen,
# changes nothing on this PC) and leaves the report and PNGs in out/selftest/.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
SDK=1.0.4314.35
SDK_SHA=90778deb1b77e36292828b4b5833c9255c27533f40e0ddd55891ae718944edc4
CSC=/mnt/c/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe

cache="${XDG_CACHE_HOME:-$HOME/.cache}/screen-machine/webview2-$SDK"
if [ ! -f "$cache/lib/net462/Microsoft.Web.WebView2.Core.dll" ]; then
  mkdir -p "$cache"
  curl -fsSL -o "$cache/sdk.nupkg" "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/$SDK/microsoft.web.webview2.$SDK.nupkg"
  echo "$SDK_SHA  $cache/sdk.nupkg" | sha256sum -c --quiet
  python3 -I -c "import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$cache/sdk.nupkg" "$cache"
fi

# csc reads Windows paths: work in Windows' temp folder
wtmp="$(wslpath "$(powershell.exe -NoProfile -Command '[IO.Path]::GetTempPath()' | tr -d '\r')")/sm-screensaver-build"
rm -rf "$wtmp" && mkdir -p "$wtmp"
cp "$here/ScreenMachineSaver.cs" "$cache/lib/net462/Microsoft.Web.WebView2.Core.dll" "$cache/runtimes/win-x64/native/WebView2Loader.dll" "$wtmp/"
(cd "$wtmp" && "$CSC" /nologo /target:winexe /platform:x64 /optimize+ /out:ScreenMachine.exe \
  /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll /r:Microsoft.Web.WebView2.Core.dll \
  /resource:Microsoft.Web.WebView2.Core.dll /resource:WebView2Loader.dll ScreenMachineSaver.cs | tr -d '\r')

mkdir -p "$here/out"
cp "$wtmp/ScreenMachine.exe" "$here/out/ScreenMachine-screensaver.exe.new"
mv "$here/out/ScreenMachine-screensaver.exe.new" "$here/out/ScreenMachine-screensaver.exe"
echo "built $here/out/ScreenMachine-screensaver.exe ($(stat -c %s "$here/out/ScreenMachine-screensaver.exe") bytes)"

if [ "${1:-}" = "--test" ]; then
  # a copy with a trailer, as the download has (server and screen from SM_SCREENSAVER_TEST_SERVER/_SCREEN)
  server="${SM_SCREENSAVER_TEST_SERVER:?set SM_SCREENSAVER_TEST_SERVER, e.g. the https address of the app}"
  screen="${SM_SCREENSAVER_TEST_SCREEN:?set SM_SCREENSAVER_TEST_SCREEN, a screen id}"
  python3 -I - "$here/out/ScreenMachine-screensaver.exe" "$wtmp/test.exe" "$server" "$screen" <<'PY'
import json, struct, sys
src, dst, server, screen = sys.argv[1:]
body = json.dumps({"server": server, "screen": screen}).encode()
open(dst, "wb").write(open(src, "rb").read() + body + struct.pack("<i", len(body)) + b"SMSS")
PY
  rm -rf "$wtmp/selftest"
  (cd "$wtmp" && ./test.exe /selftest "$(wslpath -w "$wtmp/selftest")") || true
  rm -rf "$here/out/selftest" && cp -r "$wtmp/selftest" "$here/out/selftest"
  cat "$here/out/selftest/report.txt"
fi

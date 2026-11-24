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
version="$(date -u +%Y%m%d%H%M)"  # the installed copies update themselves to a newer one (Updater, GET /api/screensaver/version)
sed "s/public const string Version = \"0\";/public const string Version = \"$version\";/" "$here/ScreenMachineSaver.cs" > "$wtmp/ScreenMachineSaver.cs"
grep -q "Version = \"$version\"" "$wtmp/ScreenMachineSaver.cs" || { echo "couldn't stamp the version" >&2; exit 1; }
cp "$cache/lib/net462/Microsoft.Web.WebView2.Core.dll" "$cache/runtimes/win-x64/native/WebView2Loader.dll" "$wtmp/"
(cd "$wtmp" && "$CSC" /nologo /target:winexe /platform:x64 /optimize+ /out:ScreenMachine.exe \
  /r:System.Windows.Forms.dll /r:System.Drawing.dll /r:System.Web.Extensions.dll /r:Microsoft.Web.WebView2.Core.dll \
  /resource:Microsoft.Web.WebView2.Core.dll /resource:WebView2Loader.dll ScreenMachineSaver.cs | tr -d '\r')

mkdir -p "$here/out"
cp "$wtmp/ScreenMachine.exe" "$here/out/ScreenMachine-screensaver.exe.new"
mv "$here/out/ScreenMachine-screensaver.exe.new" "$here/out/ScreenMachine-screensaver.exe"
echo "$version" > "$here/out/version.txt"
echo "built $here/out/ScreenMachine-screensaver.exe v$version ($(stat -c %s "$here/out/ScreenMachine-screensaver.exe") bytes)"

if [ "${1:-}" = "--test" ]; then
  # a stamped copy, as the download is (server and screen from SM_SCREENSAVER_TEST_SERVER/_SCREEN)
  server="${SM_SCREENSAVER_TEST_SERVER:?set SM_SCREENSAVER_TEST_SERVER, e.g. the https address of the app}"
  screen="${SM_SCREENSAVER_TEST_SCREEN:?set SM_SCREENSAVER_TEST_SCREEN, a screen id}"
  (cd "$here/../.." && .venv/bin/python -c 'import sys; from routes.screensaver import stamp; open(sys.argv[2], "wb").write(stamp(open(sys.argv[1], "rb").read(), sys.argv[3], sys.argv[4]))' \
    "$here/out/ScreenMachine-screensaver.exe" "$wtmp/test.exe" "$server" "$screen")
  rm -rf "$wtmp/selftest"
  (cd "$wtmp" && ./test.exe /selftest "$(wslpath -w "$wtmp/selftest")") || true
  rm -rf "$here/out/selftest" && cp -r "$wtmp/selftest" "$here/out/selftest"
  cat "$here/out/selftest/report.txt"
fi

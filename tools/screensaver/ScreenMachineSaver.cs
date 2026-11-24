// Screen Machine screensaver for Windows (tools/screensaver/README.md). One file, two names:
//   ScreenMachine-screensaver.exe   what the app's menu downloads; run with no arguments it is the installer
//   ScreenMachine.scr               the installed copy; Windows runs it as a screensaver (/s, /p <hwnd>, /c)
// It shows the app's own /display/<screen> page full screen in WebView2 (part of Windows 11), as a viewer of that
// screen. Windows' own screensaver logic decides when it starts and whether it locks; this only draws and exits.
// The download carries its server and screen in a trailer (routes/screensaver.py); Settings can change both.
// C# 5 (the compiler that ships with .NET Framework 4.8), so no newer syntax.

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Win32;

[assembly: AssemblyTitle("Screen Machine screensaver")]
[assembly: AssemblyProduct("Screen Machine")]
[assembly: AssemblyVersion("1.0.0.0")]
[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8")]  // modern TLS and runtime defaults

namespace ScreenMachine.Screensaver
{
    static class Program
    {
        public const string Version = "1";

        [STAThread]
        static int Main(string[] args)
        {
            try { Native.SetProcessDpiAwarenessContext(new IntPtr(-4)); } catch (Exception) { }  // per-monitor v2
            AppDomain.CurrentDomain.AssemblyResolve += Lib.Resolve;
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try { return Run(args); }
            catch (Exception e) { Log.Write("fatal: " + e); return 1; }
        }

        static int Run(string[] args)
        {
            // Windows passes /s, /p <hwnd>, /c:<hwnd> or /c (any case, '-' or '/', ':' or a space before the value)
            string verb = "", value = null;
            if (args.Length > 0)
            {
                verb = args[0].Trim().TrimStart('/', '-').ToLowerInvariant();
                int colon = verb.IndexOf(':');
                if (colon >= 0) { value = verb.Substring(colon + 1); verb = verb.Substring(0, colon); }
                else if (args.Length > 1) value = args[1];
            }
            Log.Write("start " + (verb == "" ? "(none)" : verb) + " from " + Self.Path);
            switch (verb)
            {
                case "s": Saver.Run(); return 0;
                case "p": Application.Run(new PreviewForm(new IntPtr(long.Parse(value)))); return 0;
                case "c": case "": Application.Run(new SettingsForm()); return 0;
                case "uninstall": Installer.Uninstall(); return 0;
                case "selftest": return SelfTest.Run(value);
                default: return 2;
            }
        }
    }

    // ------------------------------------------------------------------ where things are

    static class Self
    {
        public static string Path { get { return Application.ExecutablePath; } }

        static Dictionary<string, object> trailer;

        /// The download's own settings: [utf-8 JSON][int32 length]["SMSS"] appended to the file by the server.
        public static string Trailer(string key)
        {
            if (trailer == null) trailer = ReadTrailer();
            object v;
            return trailer.TryGetValue(key, out v) ? v as string : null;
        }

        static Dictionary<string, object> ReadTrailer()
        {
            var none = new Dictionary<string, object>();
            try
            {
                using (var f = File.OpenRead(Path))
                {
                    if (f.Length < 8) return none;
                    var tail = new byte[8];
                    f.Seek(-8, SeekOrigin.End);
                    f.Read(tail, 0, 8);
                    if (Encoding.ASCII.GetString(tail, 4, 4) != "SMSS") return none;
                    int n = BitConverter.ToInt32(tail, 0);
                    if (n <= 0 || n > 65536 || n > f.Length - 8) return none;
                    var body = new byte[n];
                    f.Seek(-8 - n, SeekOrigin.End);
                    f.Read(body, 0, n);
                    return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(body)) ?? none;
                }
            }
            catch (Exception e) { Log.Write("trailer: " + e.Message); return none; }
        }
    }

    /// What one monitor shows: a screen ("" = nothing, black) and how its picture meets the monitor's shape.
    sealed class Choice
    {
        public string Screen = "";
        public bool Fill = true;  // fill crops the overhang; fit shows all of it, with black bars
    }

    /// Settings live in HKCU\Software\ScreenMachine\Screensaver: Server, the default choice (Screen, Fit) for monitors
    /// with none of their own, and Monitors\<id> (Path, Name, Screen, Fit) for each monitor someone chose for. A monitor
    /// is known by its device path (the physical monitor on its connector), so a laptop's choices come back each time
    /// the same monitor is plugged in, whatever Windows numbers it that day.
    static class Settings
    {
        const string Key = @"Software\ScreenMachine\Screensaver";

        public static string DataDir
        {
            get { return System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ScreenMachine", "Screensaver"); }
        }

        public static string Server
        {
            get { return (Get(Key, "Server") ?? Self.Trailer("server") ?? "").Trim().TrimEnd('/'); }
            set { Set(Key, "Server", (value ?? "").Trim().TrimEnd('/')); }
        }

        /// For monitors with no choice of their own (and the ones plugged in later). The download's screen at first.
        public static Choice Default
        {
            get { return Read(Key) ?? new Choice { Screen = Self.Trailer("screen") ?? "" }; }
            set { Write(Key, value); }
        }

        public static Choice For(Monitor m)
        {
            return Read(MonitorKey(m)) ?? Default;
        }

        public static void Remember(Monitor m, Choice c)
        {
            string k = MonitorKey(m);
            Write(k, c);
            Set(k, "Path", m.Key);
            Set(k, "Name", m.Name);
        }

        /// The TV's own page, as a viewer (never drives the screen), at full brightness (no room light mask).
        public static string DisplayUrl(string server, Choice c)
        {
            if (server == "" || c.Screen == "") return null;
            return server + "/display/" + Uri.EscapeDataString(c.Screen) + "?kiosk=viewer&nomask&nocolour" + (c.Fill ? "&display=fill" : "");
        }

        static string MonitorKey(Monitor m)
        {
            using (var sha = System.Security.Cryptography.SHA1.Create())
            {
                var h = sha.ComputeHash(Encoding.UTF8.GetBytes(m.Key.ToLowerInvariant()));
                return Key + @"\Monitors\" + BitConverter.ToString(h, 0, 6).Replace("-", "").ToLowerInvariant();
            }
        }

        static Choice Read(string key)
        {
            string screen = Get(key, "Screen");
            if (screen == null) return null;
            return new Choice { Screen = screen, Fill = Get(key, "Fit") != "fit" };
        }

        static void Write(string key, Choice c)
        {
            Set(key, "Screen", c.Screen ?? "");
            Set(key, "Fit", c.Fill ? "fill" : "fit");
        }

        static string Get(string key, string name)
        {
            using (var k = Registry.CurrentUser.OpenSubKey(key)) return k == null ? null : k.GetValue(name) as string;
        }

        static void Set(string key, string name, string value)
        {
            using (var k = Registry.CurrentUser.CreateSubKey(key)) k.SetValue(name, value);
        }

        public static void Forget()
        {
            Registry.CurrentUser.DeleteSubKeyTree(@"Software\ScreenMachine", false);
        }
    }

    // ------------------------------------------------------------------ monitors

    sealed class Monitor
    {
        public string Key;      // the monitor's device path (stable), else Windows' display name
        public string Name;     // "Built-in display", "DELL U2723QE", else "Monitor 2"
        public string Device;   // \\.\DISPLAY1 (today's number)
        public Rectangle Bounds;
        public bool Primary;

        public string Describe()
        {
            return Bounds.Width + " × " + Bounds.Height + (Primary ? " · main" : "");
        }
    }

    /// The monitors attached right now, read fresh every time (a laptop's set changes through the day): Windows'
    /// screens, named and keyed by the display configuration (QueryDisplayConfig).
    static class Monitors
    {
        public static List<Monitor> Now()
        {
            var ids = Identify();
            var list = new List<Monitor>();
            int n = 0;
            foreach (Screen s in Screen.AllScreens)
            {
                n++;
                string[] id;
                ids.TryGetValue(s.DeviceName, out id);
                list.Add(new Monitor
                {
                    Key = id != null && id[0] != "" ? id[0] : s.DeviceName,
                    Name = id != null && id[1] != "" ? id[1] : "Monitor " + n,
                    Device = s.DeviceName,
                    Bounds = s.Bounds,
                    Primary = s.Primary,
                });
            }
            // the main one first, then left to right
            list.Sort((a, b) => a.Primary != b.Primary ? (a.Primary ? -1 : 1) : a.Bounds.X != b.Bounds.X ? a.Bounds.X.CompareTo(b.Bounds.X) : a.Bounds.Y.CompareTo(b.Bounds.Y));
            return list;
        }

        /// \\.\DISPLAYn -> [device path, friendly name]; empty when Windows won't say.
        static Dictionary<string, string[]> Identify()
        {
            var map = new Dictionary<string, string[]>(StringComparer.OrdinalIgnoreCase);
            try
            {
                uint np, nm;
                if (Native.GetDisplayConfigBufferSizes(2, out np, out nm) != 0) return map;  // QDC_ONLY_ACTIVE_PATHS
                var paths = new Native.PATH[np];
                var modes = new Native.MODE[nm];
                if (Native.QueryDisplayConfig(2, ref np, paths, ref nm, modes, IntPtr.Zero) != 0) return map;
                for (int i = 0; i < np; i++)
                {
                    var src = new Native.SOURCE_NAME();
                    src.header.type = 1;  // DISPLAYCONFIG_DEVICE_INFO_GET_SOURCE_NAME
                    src.header.size = (uint)Marshal.SizeOf(typeof(Native.SOURCE_NAME));
                    src.header.adapterLow = paths[i].sourceAdapterLow;
                    src.header.adapterHigh = paths[i].sourceAdapterHigh;
                    src.header.id = paths[i].sourceId;
                    if (Native.DisplayConfigGetDeviceInfo(ref src) != 0) continue;
                    var tgt = new Native.TARGET_NAME();
                    tgt.header.type = 2;  // DISPLAYCONFIG_DEVICE_INFO_GET_TARGET_NAME
                    tgt.header.size = (uint)Marshal.SizeOf(typeof(Native.TARGET_NAME));
                    tgt.header.adapterLow = paths[i].targetAdapterLow;
                    tgt.header.adapterHigh = paths[i].targetAdapterHigh;
                    tgt.header.id = paths[i].targetId;
                    if (Native.DisplayConfigGetDeviceInfo(ref tgt) != 0) continue;
                    bool builtIn = tgt.outputTechnology == 0x80000000 || tgt.outputTechnology == 11 || tgt.outputTechnology == 13;  // internal, embedded DP/UDI
                    string name = builtIn ? "Built-in display" : (tgt.friendlyName ?? "");
                    if (!map.ContainsKey(src.gdiName)) map[src.gdiName] = new[] { tgt.devicePath ?? "", name };
                }
            }
            catch (Exception e) { Log.Write("monitors: " + e.Message); }
            return map;
        }
    }

    static class Log
    {
        public static void Write(string line)
        {
            try
            {
                Directory.CreateDirectory(Settings.DataDir);
                string p = Path.Combine(Settings.DataDir, "screensaver.log");
                if (File.Exists(p) && new FileInfo(p).Length > 512 * 1024) File.Delete(p);
                File.AppendAllText(p, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + Process.GetCurrentProcess().Id + " " + line + "\r\n");
            }
            catch (Exception) { }
        }
    }

    // ------------------------------------------------------------------ WebView2

    /// The WebView2 SDK's managed DLL and its native loader travel inside this file; they are written out once.
    static class Lib
    {
        static string dir;

        public static string Dir
        {
            get
            {
                if (dir != null) return dir;
                string d = Path.Combine(Settings.DataDir, "lib-" + Program.Version + "-" + typeof(Lib).Assembly.ManifestModule.ModuleVersionId.ToString("N").Substring(0, 8));
                Directory.CreateDirectory(d);
                foreach (string name in new[] { "Microsoft.Web.WebView2.Core.dll", "WebView2Loader.dll" })
                {
                    string p = Path.Combine(d, name);
                    if (File.Exists(p)) continue;
                    string tmp = p + "." + Process.GetCurrentProcess().Id + ".tmp";
                    using (Stream s = typeof(Lib).Assembly.GetManifestResourceStream(name))
                    using (var o = File.Create(tmp)) s.CopyTo(o);
                    try { File.Move(tmp, p); } catch (IOException) { File.Delete(tmp); }  // another copy got there first
                }
                return dir = d;
            }
        }

        public static Assembly Resolve(object sender, ResolveEventArgs e)
        {
            if (!e.Name.StartsWith("Microsoft.Web.WebView2.Core")) return null;
            return Assembly.LoadFrom(Path.Combine(Dir, "Microsoft.Web.WebView2.Core.dll"));
        }
    }

    static class Web
    {
        static Task<CoreWebView2Environment> env;
        static bool loaderSet;

        public static void UseOurLoader()
        {
            if (loaderSet) return;
            CoreWebView2Environment.SetLoaderDllFolderPath(Lib.Dir);
            loaderSet = true;
        }

        /// One profile for the screensaver and its Settings (so a sign-in there is the screensaver's too).
        public static Task<CoreWebView2Environment> Env(string profile, string extraArgs)
        {
            if (env == null)
            {
                UseOurLoader();
                var opts = new CoreWebView2EnvironmentOptions();
                opts.AdditionalBrowserArguments = "--autoplay-policy=no-user-gesture-required" + (extraArgs ?? "");
                env = CoreWebView2Environment.CreateAsync(null, Path.Combine(Settings.DataDir, profile), opts);
            }
            return env;
        }

        /// On every page the saver loads, from the first instant: a black page, no cursor, and a black cover over it
        /// that fades away once the art is on screen (a shown picture or a playing video, or after 8 s regardless). The
        /// app's own background is light, and a picture paints in strips as it decodes; neither is ever seen.
        const string Smooth = @"(function () {
  if (window.top !== window) return;
  var css = document.createElement('style');
  css.textContent = 'html,body{background:#000!important}*{cursor:none!important}' +
    '#sm-saver-cover{position:fixed;left:0;top:0;right:0;bottom:0;background:#000;z-index:2147483647;' +
    'pointer-events:none;opacity:1;transition:opacity 1.5s ease-in-out}';
  var cover = document.createElement('div');
  cover.id = 'sm-saver-cover';
  (function put() {
    var r = document.documentElement;
    if (!r) return setTimeout(put, 0);
    r.appendChild(css);
    r.appendChild(cover);
  })();
  function big(el) {
    var b = el.getBoundingClientRect(), st = getComputedStyle(el);
    return b.width > innerWidth / 3 && b.height > innerHeight / 3 && st.visibility !== 'hidden' && parseFloat(st.opacity) > 0.5;
  }
  function ready() {
    var i, m = document.images, v = document.querySelectorAll('video');
    for (i = 0; i < m.length; i++) if (m[i].complete && m[i].naturalWidth > 0 && big(m[i])) return true;
    for (i = 0; i < v.length; i++) if (v[i].readyState >= 2 && big(v[i])) return true;
    return false;
  }
  var t0 = Date.now();
  (function check() {
    if (!ready() && Date.now() - t0 < 8000) return setTimeout(check, 100);
    window.__smSaverReadyAfterMs = Date.now() - t0;
    requestAnimationFrame(function () { requestAnimationFrame(function () {
      cover.style.opacity = '0';
      setTimeout(function () { cover.remove(); }, 2000);
    }); });
  })();
})();";

        /// The saver page's input, which the page never sees: everything the person does is stopped at the window
        /// (capture phase, before the app's own handlers) and sent here, so a touch, click or mouse move ends the
        /// screensaver instead of opening the page's hover or tap controls. Touch never reaches the low-level mouse
        /// hook (the browser takes pointer input itself), so this is the only way a tap is seen.
        const string Shield = @"(function () {
  if (window.top !== window) return;
  var x0 = null, y0 = null, sent = false;
  function quit(why) {
    if (sent) return;
    sent = true;
    try { window.chrome.webview.postMessage(why); } catch (e) {}
  }
  function eat(e) { e.stopImmediatePropagation(); if (e.cancelable) e.preventDefault(); }
  ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'click', 'dblclick', 'contextmenu', 'touchstart', 'touchend',
   'wheel', 'keydown', 'keyup'].forEach(function (t) {
    window.addEventListener(t, function (e) { eat(e); quit(t + (e.pointerType ? ':' + e.pointerType : '')); },
      { capture: true, passive: false });
  });
  ['pointerover', 'pointerenter', 'pointerout', 'pointerleave', 'mouseover', 'mouseenter', 'mouseout', 'mouseleave',
   'mousemove', 'touchmove'].forEach(function (t) {
    window.addEventListener(t, eat, { capture: true, passive: false });
  });
  window.addEventListener('pointermove', function (e) {
    eat(e);
    if (e.pointerType && e.pointerType !== 'mouse') return quit('pointermove:' + e.pointerType);
    if (x0 === null) { x0 = e.screenX; y0 = e.screenY; return; }  // where the cursor was when the page appeared
    if (Math.abs(e.screenX - x0) + Math.abs(e.screenY - y0) > 12) quit('pointermove:mouse');
  }, { capture: true, passive: false });
})();";

        /// Where a saver page's input goes (Saver sets it; the self-test leaves it unset).
        public static Action<string> PageInput;

        /// A page filling `host`. `saver`: no menus, keys, popups or sound, no cursor; a failed load stays black and
        /// tries again every 30 s (the page itself rides out the server going away once it has loaded).
        public static async Task<CoreWebView2Controller> Attach(Control host, string url, bool saver, string profile, string extraArgs)
        {
            var e = await Env(profile, extraArgs);
            var c = await e.CreateCoreWebView2ControllerAsync(host.Handle);
            c.DefaultBackgroundColor = Color.Black;
            if (saver) c.IsVisible = false;  // shown once the page has loaded (and is black under its cover)
            c.Bounds = host.ClientRectangle;
            host.Resize += delegate { c.Bounds = host.ClientRectangle; };
            host.Move += delegate { c.NotifyParentWindowPositionChanged(); };  // moved to a monitor of another scale
            host.Disposed += delegate { c.Close(); };
            var w = c.CoreWebView2;
            if (saver)
            {
                w.Settings.AreDefaultContextMenusEnabled = false;
                w.Settings.AreDevToolsEnabled = false;
                w.Settings.IsStatusBarEnabled = false;
                w.Settings.IsZoomControlEnabled = false;
                w.Settings.AreBrowserAcceleratorKeysEnabled = false;
                w.IsMuted = true;
                w.NewWindowRequested += delegate(object s, CoreWebView2NewWindowRequestedEventArgs a) { a.Handled = true; };
                await w.AddScriptToExecuteOnDocumentCreatedAsync(Smooth);
                await w.AddScriptToExecuteOnDocumentCreatedAsync(Shield);
                w.WebMessageReceived += delegate(object s, CoreWebView2WebMessageReceivedEventArgs a)
                {
                    var h = PageInput;
                    if (h == null) return;
                    string why;
                    try { why = a.TryGetWebMessageAsString(); } catch { why = "?"; }
                    h(why);
                };
                var retry = new Timer { Interval = 30000 };
                retry.Tick += delegate { retry.Stop(); if (!host.IsDisposed) w.Navigate(url); };
                host.Disposed += delegate { retry.Dispose(); };
                w.NavigationCompleted += delegate(object s, CoreWebView2NavigationCompletedEventArgs a)
                {
                    c.IsVisible = a.IsSuccess;
                    if (!a.IsSuccess) { Log.Write("load failed: " + a.WebErrorStatus + " " + url); retry.Start(); }
                };
            }
            w.Navigate(url);
            return c;
        }
    }

    // ------------------------------------------------------------------ the screensaver (/s)

    /// One window per monitor, each with its own page (or black, for a monitor set to show nothing). The monitors are
    /// read when Windows starts it and again whenever they change while it runs (a laptop docked or a lid shut): a
    /// window whose monitor stays keeps its page and just moves; a new monitor gets its window; a gone one's closes.
    static class Saver
    {
        static readonly Dictionary<string, SaverForm> windows = new Dictionary<string, SaverForm>();
        static Control ui;
        static Timer settle;

        public static void Run()
        {
            Input.Start();
            Web.PageInput = Input.FromPage;
            ui = new Control();
            ui.CreateControl();  // marshals Windows' display-change event onto this thread
            settle = new Timer { Interval = 1000 };  // a dock or undock changes things several times over a second
            settle.Tick += delegate { settle.Stop(); Arrange(); };
            SystemEvents.DisplaySettingsChanged += delegate { ui.BeginInvoke((Action)(() => { settle.Stop(); settle.Start(); })); };
            Arrange();
            Cursor.Hide();
            Application.Run();
            Input.Stop();
            Environment.Exit(0);  // the browser processes go with us
        }

        static void Arrange()
        {
            string server = Settings.Server;
            var now = Monitors.Now();
            bool anything = false;
            foreach (var m in now) if (Settings.DisplayUrl(server, Settings.For(m)) != null) anything = true;
            var seen = new HashSet<string>();
            foreach (var m in now)
            {
                if (!seen.Add(m.Key)) continue;  // mirrored monitors: one window covers both
                string url = Settings.DisplayUrl(server, Settings.For(m));
                string note = anything ? null : "Choose a screen in the Screen Machine screensaver's settings.";
                SaverForm w;
                if (windows.TryGetValue(m.Key, out w) && w.Url == url)
                {
                    if (w.Bounds != m.Bounds) w.Bounds = m.Bounds;
                    continue;
                }
                if (w != null) w.Close();
                Log.Write("monitor " + m.Name + " (" + m.Describe() + ", " + m.Key + "): " + (url ?? "black"));
                w = new SaverForm(m.Bounds, url, note);
                windows[m.Key] = w;
                w.Show();
            }
            foreach (var k in new List<string>(windows.Keys))
                if (!seen.Contains(k)) { Log.Write("monitor gone: " + k); windows[k].Close(); windows.Remove(k); }
        }
    }

    sealed class SaverForm : Form
    {
        public readonly string Url;
        readonly string note;

        public SaverForm(Rectangle bounds, string url, string note)
        {
            Url = url;
            this.note = note;
            FormBorderStyle = FormBorderStyle.None;
            StartPosition = FormStartPosition.Manual;
            Bounds = bounds;
            BackColor = Color.Black;
            ForeColor = Color.Gray;
            TopMost = true;
            ShowInTaskbar = false;
            Text = "Screen Machine";
        }

        // Cloaked until it has painted black, so the screen snaps straight to black (never a frame of white).
        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            Native.Cloak(Handle, true);
        }

        protected override async void OnShown(EventArgs e)
        {
            base.OnShown(e);
            Refresh();
            Native.Cloak(Handle, false);
            if (Url == null) return;
            try { await Web.Attach(this, Url, true, "WebView2", null); }
            catch (Exception x) { Log.Write("webview: " + x.Message); }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            if (note == null) return;
            TextRenderer.DrawText(e.Graphics, note, new Font("Segoe UI", 14f), ClientRectangle, ForeColor,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
        }
    }

    /// Any key, click or wheel, or the mouse moving more than a few pixels, ends it. The page's window belongs to the
    /// browser process, so its input never reaches our forms: low-level hooks see it all. Windows drops a hook whose
    /// callback is slow, so a timer also watches the cursor.
    static class Input
    {
        delegate IntPtr HookProc(int code, IntPtr wParam, IntPtr lParam);

        static HookProc keyProc, mouseProc;
        static IntPtr keyHook, mouseHook;
        static Point origin;
        static DateTime started;
        static Timer watch;

        public static void Start()
        {
            started = DateTime.UtcNow;
            origin = Cursor.Position;
            keyProc = OnKey;
            mouseProc = OnMouse;
            IntPtr mod = Native.GetModuleHandle(null);
            keyHook = Native.SetWindowsHookEx(13, keyProc, mod, 0);    // WH_KEYBOARD_LL
            mouseHook = Native.SetWindowsHookEx(14, mouseProc, mod, 0);  // WH_MOUSE_LL
            watch = new Timer { Interval = 200 };
            watch.Tick += delegate { if (Moved(Cursor.Position)) Quit("cursor"); };
            watch.Start();
        }

        public static void Stop()
        {
            if (keyHook != IntPtr.Zero) Native.UnhookWindowsHookEx(keyHook);
            if (mouseHook != IntPtr.Zero) Native.UnhookWindowsHookEx(mouseHook);
        }

        static bool Moved(Point p) { return Math.Abs(p.X - origin.X) + Math.Abs(p.Y - origin.Y) > 12; }

        static IntPtr OnKey(int code, IntPtr wParam, IntPtr lParam)
        {
            if (code >= 0) Quit("key");
            return Native.CallNextHookEx(keyHook, code, wParam, lParam);
        }

        static IntPtr OnMouse(int code, IntPtr wParam, IntPtr lParam)
        {
            if (code >= 0)
            {
                if (wParam.ToInt32() != 0x0200) Quit("button");  // anything but WM_MOUSEMOVE
                else
                {
                    var m = (Native.MSLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(Native.MSLLHOOKSTRUCT));
                    if (Moved(new Point(m.x, m.y))) Quit("mouse");
                }
            }
            return Native.CallNextHookEx(mouseHook, code, wParam, lParam);
        }

        public static void FromPage(string why) { Quit("page " + why); }

        static bool quitting;

        /// Once: the windows vanish at once (cloaked), then everything closes.
        static void Quit(string why)
        {
            if (quitting) return;
            if ((DateTime.UtcNow - started).TotalMilliseconds < 800) return;  // the click on "Preview" and its release
            quitting = true;
            Log.Write("exit: " + why);
            foreach (Form f in Application.OpenForms) try { Native.Cloak(f.Handle, true); } catch { }
            Application.Exit();
        }
    }

    // ------------------------------------------------------------------ the little monitor in Windows' dialog (/p)

    /// The screen's current picture (its thumbnail), else its name. No browser here: the dialog starts and kills the
    /// preview every time someone looks at the list.
    sealed class PreviewForm : Form
    {
        readonly IntPtr parent;
        Image picture;
        Choice choice;

        public PreviewForm(IntPtr parent)
        {
            this.parent = parent;
            FormBorderStyle = FormBorderStyle.None;
            BackColor = Color.Black;
            ShowInTaskbar = false;
            StartPosition = FormStartPosition.Manual;
            DoubleBuffered = true;
        }

        protected override void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            Native.SetParent(Handle, parent);
            Native.SetWindowLong(Handle, -16, Native.GetWindowLong(Handle, -16) | 0x40000000);  // GWL_STYLE |= WS_CHILD
            Native.RECT r;
            Native.GetClientRect(parent, out r);
            Bounds = new Rectangle(0, 0, r.Right, r.Bottom);
            var alive = new Timer { Interval = 500 };
            alive.Tick += delegate { if (!Native.IsWindow(parent)) Close(); };
            alive.Start();
            var mons = Monitors.Now();
            if (mons.Count == 0) return;
            string server = Settings.Server;
            choice = Settings.For(mons[0]);  // the main monitor's
            if (server == "" || choice.Screen == "") return;
            string screen = choice.Screen;
            Task.Run(() => Thumbnail(server, screen)).ContinueWith(t =>
            {
                if (t.Result != null && !IsDisposed) BeginInvoke((Action)(() => { picture = t.Result; Invalidate(); }));
            });
        }

        public static Image Thumbnail(string server, string screen)
        {
            try
            {
                using (var wc = new WebClient())
                {
                    wc.Encoding = Encoding.UTF8;
                    var info = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(
                        wc.DownloadString(server + "/api/buckets/" + Uri.EscapeDataString(screen) + "/info"));
                    object u;
                    if (!info.TryGetValue("thumbnail_url", out u) || !(u is string)) return null;
                    using (var ms = new MemoryStream(wc.DownloadData(server + (string)u)))
                    using (var img = Image.FromStream(ms))
                        return new Bitmap(img);  // GDI+ needs its stream for the image's life: keep a copy instead
                }
            }
            catch (Exception e) { Log.Write("thumbnail: " + e.Message); return null; }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            if (picture != null)
            {
                // as the monitor will: filled (cropping the overhang) or fitted (black bars)
                float sx = (float)Width / picture.Width, sy = (float)Height / picture.Height;
                float s = choice.Fill ? Math.Max(sx, sy) : Math.Min(sx, sy);
                float w = picture.Width * s, h = picture.Height * s;
                e.Graphics.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                e.Graphics.DrawImage(picture, (Width - w) / 2, (Height - h) / 2, w, h);
                return;
            }
            TextRenderer.DrawText(e.Graphics, "Screen Machine", new Font("Segoe UI", 8f), ClientRectangle, Color.Gray,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.WordBreak);
        }
    }

    // ------------------------------------------------------------------ Settings, and the installer

    sealed class ScreenChoice
    {
        public string Id, Name, Access;
        public override string ToString() { return Name; }
    }

    /// One row of Settings: a monitor attached now (or, with no monitor, the ones plugged in later) and its choice.
    sealed class MonitorRow
    {
        public Monitor Monitor;  // null: monitors plugged in later
        public Choice Was;
        public ComboBox Screen = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 230, Enabled = false };
        public ComboBox Fit = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 140 };
    }

    /// The one window: what each monitor shows, and from where. Run from the download it installs; run from Windows'
    /// dialog ("Settings...") it saves. Both keep the screensaver set up the Windows way (Windows' dialog sets the wait
    /// and the lock). The monitors are the ones attached as it opens; each one's choice is kept for the next time that
    /// monitor is attached, and "plugged in later" covers any other.
    sealed class SettingsForm : Form
    {
        static readonly ScreenChoice Nothing = new ScreenChoice { Id = "", Name = "Nothing (black)", Access = "guest" };
        const string FillWords = "Fill (crops edges)", FitWords = "Fit (black bars)";

        readonly List<MonitorRow> rows = new List<MonitorRow>();
        readonly TextBox server = new TextBox { Width = 380 };
        readonly Label note = new Label { AutoSize = true, MaximumSize = new Size(480, 0), ForeColor = Color.DimGray };
        readonly LinkLabel signIn = new LinkLabel { AutoSize = true, Text = "Sign in", Visible = false };
        readonly Button ok = new Button { AutoSize = true, Padding = new Padding(8, 2, 8, 2) };
        readonly Button remove = new Button { AutoSize = true, Text = "Remove", Padding = new Padding(8, 2, 8, 2) };
        readonly Button cancel = new Button { AutoSize = true, Text = "Cancel", DialogResult = DialogResult.Cancel, Padding = new Padding(8, 2, 8, 2) };
        readonly bool installing;
        int loading;

        public SettingsForm()
        {
            installing = !Installer.IsInstalledCopy || !Installer.IsCurrentScreensaver;
            Text = "Screen Machine screensaver";
            Font = new Font("Segoe UI", 10f);
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = MinimizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            AutoSize = true;
            AutoSizeMode = AutoSizeMode.GrowAndShrink;
            Padding = new Padding(16);
            AcceptButton = ok;
            CancelButton = cancel;

            var col = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, WrapContents = false };
            var head = new FlowLayoutPanel { AutoSize = true, WrapContents = false, Margin = new Padding(0, 0, 0, 8) };
            head.Controls.Add(new Label { AutoSize = true, Text = "When the computer is idle, show", Font = new Font("Segoe UI Semibold", 11f) });
            var which = new LinkLabel { AutoSize = true, Text = "Which monitor is which?", Margin = new Padding(12, 4, 0, 0) };
            which.LinkClicked += delegate { Identify(); };
            head.Controls.Add(which);
            col.Controls.Add(head);

            var grid = new TableLayoutPanel { AutoSize = true, ColumnCount = 3, Margin = new Padding(0) };
            var mons = Monitors.Now();
            foreach (var m in mons) rows.Add(new MonitorRow { Monitor = m, Was = Settings.For(m) });
            rows.Add(new MonitorRow { Monitor = null, Was = Settings.Default });
            int n = 0;
            foreach (var r in rows)
            {
                n++;
                var name = new Label { AutoSize = true, Margin = new Padding(0, 6, 16, 6), MaximumSize = new Size(230, 0) };
                name.Text = r.Monitor == null ? "Monitors plugged in later" : n + "  " + r.Monitor.Name + "\n    " + r.Monitor.Describe();
                if (r.Monitor == null) name.ForeColor = Color.DimGray;
                r.Fit.Items.AddRange(new object[] { FillWords, FitWords });
                r.Fit.SelectedIndex = r.Was.Fill ? 0 : 1;
                r.Screen.SelectedIndexChanged += delegate { ShowAccess(); };
                grid.Controls.Add(name);
                grid.Controls.Add(r.Screen);
                grid.Controls.Add(r.Fit);
            }
            col.Controls.Add(grid);
            var noteRow = new FlowLayoutPanel { AutoSize = true, WrapContents = false, Margin = new Padding(0, 6, 0, 12) };
            noteRow.Controls.Add(note);
            noteRow.Controls.Add(signIn);
            col.Controls.Add(noteRow);
            col.Controls.Add(new Label { AutoSize = true, Text = "From", ForeColor = Color.DimGray });
            col.Controls.Add(server);
            var buttons = new FlowLayoutPanel { AutoSize = true, FlowDirection = FlowDirection.RightToLeft, Dock = DockStyle.Fill, Margin = new Padding(0, 16, 0, 0) };
            buttons.Controls.Add(cancel);
            buttons.Controls.Add(ok);
            if (Installer.IsInstalled) buttons.Controls.Add(remove);
            col.Controls.Add(buttons);
            Controls.Add(col);

            ok.Text = installing ? "Use as screensaver" : "Save";
            server.Text = Settings.Server;
            server.Leave += delegate { LoadScreens(); };
            ok.Click += delegate { Save(); };
            remove.Click += delegate
            {
                if (MessageBox.Show(this, "Remove the Screen Machine screensaver from this computer?", Text, MessageBoxButtons.OKCancel) != DialogResult.OK) return;
                Installer.Uninstall();
                Close();
            };
            signIn.LinkClicked += delegate { new SignInForm(server.Text.Trim().TrimEnd('/')).ShowDialog(this); LoadScreens(); };
            Shown += delegate { LoadScreens(); };
        }

        /// A big number on each monitor for two seconds, matching the rows.
        void Identify()
        {
            int n = 0;
            foreach (var r in rows)
            {
                if (r.Monitor == null) continue;
                n++;
                var tag = new Form
                {
                    FormBorderStyle = FormBorderStyle.None, StartPosition = FormStartPosition.Manual, ShowInTaskbar = false, TopMost = true,
                    BackColor = Color.FromArgb(17, 94, 89), ForeColor = Color.White, Size = new Size(220, 220),
                };
                tag.Location = new Point(r.Monitor.Bounds.Left + 40, r.Monitor.Bounds.Top + 40);
                tag.Controls.Add(new Label { Dock = DockStyle.Fill, Text = n.ToString(), Font = new Font("Segoe UI Semibold", 96f), TextAlign = ContentAlignment.MiddleCenter });
                var t = new Timer { Interval = 2000 };
                t.Tick += delegate { t.Dispose(); tag.Close(); };
                tag.Show(this);
                t.Start();
            }
        }

        void LoadScreens()
        {
            string from = server.Text.Trim().TrimEnd('/');
            int ticket = ++loading;
            note.Text = from == "" ? "Type the Screen Machine address below." : "Finding the screens...";
            signIn.Visible = false;
            if (from == "") return;
            Task.Run(() => Fetch(from)).ContinueWith(t =>
            {
                if (IsDisposed) return;
                BeginInvoke((Action)(() =>
                {
                    if (ticket != loading) return;
                    if (t.Result == null) { note.Text = "Couldn't reach " + from + "."; return; }
                    foreach (var r in rows)
                    {
                        // keep what's picked (a second load after signing in), else what was saved
                        var picked = r.Screen.SelectedItem as ScreenChoice;
                        string want = picked != null ? picked.Id : r.Was.Screen;
                        r.Screen.Items.Clear();
                        r.Screen.Items.Add(Nothing);
                        foreach (var s in t.Result)
                        {
                            r.Screen.Items.Add(s);
                            if (s.Id == want) r.Screen.SelectedItem = s;
                        }
                        if (want == "") r.Screen.SelectedItem = Nothing;
                        if (r.Screen.SelectedIndex < 0) r.Screen.SelectedIndex = r.Screen.Items.Count > 1 ? 1 : 0;
                        r.Screen.Enabled = true;
                    }
                    ShowAccess();
                }));
            });
        }

        static List<ScreenChoice> Fetch(string from)
        {
            try
            {
                using (var wc = new WebClient())
                {
                    wc.Encoding = Encoding.UTF8;
                    var list = new JavaScriptSerializer().Deserialize<List<Dictionary<string, object>>>(wc.DownloadString(from + "/api/publish-destinations"));
                    var out_ = new List<ScreenChoice>();
                    foreach (var d in list)
                    {
                        if (True(d, "headless") || True(d, "hidden") || !d.ContainsKey("id")) continue;
                        string id = d["id"] as string;
                        if (id == null || id.StartsWith("_")) continue;
                        out_.Add(new ScreenChoice { Id = id, Name = (d.ContainsKey("name") ? d["name"] as string : null) ?? id, Access = d.ContainsKey("access") ? d["access"] as string : null });
                    }
                    return out_;
                }
            }
            catch (Exception e) { Log.Write("screens: " + e.Message); return null; }
        }

        static bool True(Dictionary<string, object> d, string k) { object v; return d.TryGetValue(k, out v) && v is bool && (bool)v; }

        void ShowAccess()
        {
            bool open = true;
            foreach (var r in rows)
            {
                var s = r.Screen.SelectedItem as ScreenChoice;
                if (s != null && s.Access != "guest") open = false;
            }
            note.Text = open ? "Each shows whatever that screen shows, live included." : "A screen you chose is for signed-in people.";
            signIn.Visible = !open;
        }

        static Choice Of(MonitorRow r)
        {
            var s = r.Screen.SelectedItem as ScreenChoice;
            return new Choice { Screen = s == null ? r.Was.Screen : s.Id, Fill = r.Fit.SelectedIndex != 1 };
        }

        void Save()
        {
            bool any = false;
            foreach (var r in rows) if (Of(r).Screen != "") any = true;
            if (!any) { MessageBox.Show(this, "Choose a screen for at least one monitor.", Text); return; }
            Settings.Server = server.Text;
            foreach (var r in rows)
            {
                if (r.Monitor == null) Settings.Default = Of(r);
                else Settings.Remember(r.Monitor, Of(r));
            }
            if (installing)
            {
                string err = Installer.Install();
                if (err != null) { MessageBox.Show(this, err, Text); return; }
                Installer.OpenWindowsSettings();  // the wait, and "On resume, display logon screen", are set there
            }
            Close();
        }
    }

    /// The app in a window, on the screensaver's own profile: signing in here signs the screensaver in.
    sealed class SignInForm : Form
    {
        readonly string server;

        public SignInForm(string server)
        {
            this.server = server;
            Text = "Sign in to Screen Machine, then close this window";
            Size = new Size(1000, 760);
            StartPosition = FormStartPosition.CenterParent;
        }

        protected override async void OnShown(EventArgs e)
        {
            base.OnShown(e);
            try { await Web.Attach(this, server + "/", false, "WebView2", null); }
            catch (Exception x) { MessageBox.Show(this, "Couldn't open the browser: " + x.Message, Text); Close(); }
        }
    }

    static class Installer
    {
        const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\ScreenMachineScreensaver";

        // no spaces in the folder or the name: Windows starts SCRNSAVE.EXE unquoted
        public static string Target
        {
            get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "ScreenMachine", "ScreenMachine.scr"); }
        }

        public static bool IsInstalled { get { return File.Exists(Target); } }

        public static bool IsInstalledCopy { get { return Same(Self.Path, Target); } }

        public static bool IsCurrentScreensaver
        {
            get
            {
                using (var k = Registry.CurrentUser.OpenSubKey(@"Control Panel\Desktop"))
                {
                    string v = k == null ? null : k.GetValue("SCRNSAVE.EXE") as string;
                    return v != null && Same(v, Target);
                }
            }
        }

        static bool Same(string a, string b)
        {
            try { return string.Equals(Path.GetFullPath(Long(a)), Path.GetFullPath(b), StringComparison.OrdinalIgnoreCase); }
            catch (Exception) { return false; }
        }

        static string Long(string p)
        {
            var sb = new StringBuilder(1024);
            return Native.GetLongPathName(p, sb, sb.Capacity) > 0 ? sb.ToString() : p;
        }

        static string Short(string p)
        {
            var sb = new StringBuilder(1024);
            return Native.GetShortPathName(p, sb, sb.Capacity) > 0 ? sb.ToString() : p;
        }

        /// Copy into the user's programs, make it the screensaver (per user: no admin), list it in Apps. Null when done.
        public static string Install()
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(Target));
                if (!IsInstalledCopy) File.Copy(Self.Path, Target, true);
                Native.DeleteFile(Target + ":Zone.Identifier");  // the download's mark, or Windows asks before each start
            }
            catch (IOException e) { return "Couldn't copy the screensaver (is it running?): " + e.Message; }
            catch (UnauthorizedAccessException e) { return "Couldn't copy the screensaver: " + e.Message; }

            string path = Target.Contains(" ") ? Short(Target) : Target;  // a user folder with a space in its name
            using (var k = Registry.CurrentUser.CreateSubKey(@"Control Panel\Desktop"))
            {
                k.SetValue("SCRNSAVE.EXE", path);
                k.SetValue("ScreenSaveActive", "1");
            }
            Native.SystemParametersInfo(0x0011, 1, IntPtr.Zero, 0x3);  // SPI_SETSCREENSAVEACTIVE, update ini + broadcast
            using (var k = Registry.CurrentUser.CreateSubKey(UninstallKey))
            {
                k.SetValue("DisplayName", "Screen Machine screensaver");
                k.SetValue("Publisher", "Screen Machine");
                k.SetValue("DisplayVersion", Program.Version);
                k.SetValue("DisplayIcon", Target);
                k.SetValue("InstallLocation", Path.GetDirectoryName(Target));
                k.SetValue("UninstallString", "\"" + Target + "\" /uninstall");
                k.SetValue("NoModify", 1, RegistryValueKind.DWord);
                k.SetValue("NoRepair", 1, RegistryValueKind.DWord);
            }
            using (var k = Registry.CurrentUser.OpenSubKey(@"Software\Policies\Microsoft\Windows\Control Panel\Desktop"))
                if (k != null && k.GetValue("SCRNSAVE.EXE") != null)
                    return "Installed, but this computer's policy chooses its own screensaver, so Windows won't use this one.";
            Log.Write("installed " + path);
            return null;
        }

        public static void OpenWindowsSettings()
        {
            try { Process.Start("control.exe", "desk.cpl,,@screensaver"); } catch (Exception) { }
        }

        public static void Uninstall()
        {
            bool current = IsCurrentScreensaver;
            using (var k = Registry.CurrentUser.CreateSubKey(@"Control Panel\Desktop"))
                if (current)
                {
                    k.DeleteValue("SCRNSAVE.EXE", false);
                    k.SetValue("ScreenSaveActive", "0");
                }
            if (current) Native.SystemParametersInfo(0x0011, 0, IntPtr.Zero, 0x3);
            Registry.CurrentUser.DeleteSubKeyTree(UninstallKey, false);
            Settings.Forget();
            Log.Write("removed");
            // this file may be the one running: a hidden cmd deletes the folders once we have exited
            string dirs = "\"" + Path.GetDirectoryName(Target) + "\" \"" + Settings.DataDir + "\"";
            var p = new ProcessStartInfo("cmd.exe", "/c timeout /t 3 /nobreak >nul & rmdir /s /q " + dirs)
            {
                CreateNoWindow = true,
                UseShellExecute = false,
                WindowStyle = ProcessWindowStyle.Hidden,
            };
            Process.Start(p);
        }
    }

    // ------------------------------------------------------------------ a check that touches nothing on the machine

    /// /selftest <folder>: reads the settings and the screen list, draws Settings and the preview to PNGs, loads the
    /// page off-screen on a profile of its own for 25 s and saves what it drew. No registry or screensaver changes.
    static class SelfTest
    {
        public static int Run(string outDir)
        {
            Directory.CreateDirectory(outDir);
            var report = new StringBuilder();
            report.AppendLine("server=" + Settings.Server + " trailer.server=" + Self.Trailer("server") + " trailer.screen=" + Self.Trailer("screen"));
            var mons = Monitors.Now();
            foreach (var m in mons)
            {
                var c = Settings.For(m);
                report.AppendLine("monitor " + m.Name + " | " + m.Describe() + " | " + m.Device + " | " + m.Key + " -> " + (c.Screen == "" ? "nothing" : c.Screen) + (c.Fill ? " fill" : " fit"));
            }
            var first = mons.Count > 0 ? Settings.For(mons[0]) : Settings.Default;
            report.AppendLine("installed=" + Installer.IsInstalled + " current=" + Installer.IsCurrentScreensaver + " target=" + Installer.Target);
            report.AppendLine("webview2 runtime=" + SafeVersion());

            var form = new SettingsForm();
            form.StartPosition = FormStartPosition.Manual;
            form.Location = new Point(-5000, -5000);
            form.ShowInTaskbar = false;
            var shot = new Timer { Interval = 4000 };
            shot.Tick += delegate
            {
                shot.Stop();
                using (var b = new Bitmap(form.Width, form.Height)) { form.DrawToBitmap(b, new Rectangle(Point.Empty, form.Size)); b.Save(Path.Combine(outDir, "settings.png")); }
                form.Close();
            };
            shot.Start();
            Application.Run(form);

            var thumb = PreviewForm.Thumbnail(Settings.Server, first.Screen);
            report.AppendLine("thumbnail=" + (thumb == null ? "none" : thumb.Width + "x" + thumb.Height));
            if (thumb != null) thumb.Save(Path.Combine(outDir, "preview.png"));

            string url = Settings.DisplayUrl(Settings.Server, first) ?? "about:blank";
            report.AppendLine("url=" + url);
            var page = new Form
            {
                FormBorderStyle = FormBorderStyle.None, StartPosition = FormStartPosition.Manual, ShowInTaskbar = false,
                Bounds = new Rectangle(-4000, -4000, 1280, 720), BackColor = Color.Black,
            };
            page.Shown += async delegate
            {
                try
                {
                    var c = await Web.Attach(page, url, true, "WebView2-selftest", " --disable-features=CalculateNativeWinOcclusion");
                    // the start, frame by frame: everything before the art is ready must be black, then a fade
                    var clock = Stopwatch.StartNew();
                    while (clock.ElapsedMilliseconds < 9000)
                    {
                        await Task.Delay(150);
                        report.AppendLine("t=" + clock.ElapsedMilliseconds + "ms visible=" + c.IsVisible + " " + await Frame(c));
                    }
                    report.AppendLine("readyAfterMs=" + await c.CoreWebView2.ExecuteScriptAsync("window.__smSaverReadyAfterMs"));
                    // a tap must reach us and never the app (whose tap opens its controls)
                    string got = null;
                    c.CoreWebView2.WebMessageReceived += delegate(object s, CoreWebView2WebMessageReceivedEventArgs a) { got = a.TryGetWebMessageAsString(); };
                    string leaked = await c.CoreWebView2.ExecuteScriptAsync(
                        "(function(){var n=0,f=function(){n++};document.addEventListener('pointerdown',f,true);" +
                        "document.body.dispatchEvent(new PointerEvent('pointerdown',{bubbles:true,pointerType:'touch'}));" +
                        "document.removeEventListener('pointerdown',f,true);return n})()");
                    await Task.Delay(300);
                    report.AppendLine("shield: tap sent=" + (got ?? "nothing") + " reached page=" + leaked + (got != null && leaked == "0" ? " OK" : " FAIL"));
                    await Task.Delay(12000);
                    string state = await c.CoreWebView2.ExecuteScriptAsync(
                        "JSON.stringify({title:document.title,imgs:[...document.images].map(i=>i.currentSrc+' '+getComputedStyle(i).objectFit).slice(0,4)," +
                        "videos:[...document.querySelectorAll('video')].map(v=>({src:v.currentSrc,paused:v.paused,w:v.videoWidth})),cursor:getComputedStyle(document.body).cursor})");
                    report.AppendLine("page=" + state);
                    using (var f = File.Create(Path.Combine(outDir, "page.png")))
                        await c.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, f);
                }
                catch (Exception x) { report.AppendLine("page error: " + x); }
                page.Close();
            };
            Application.Run(page);
            File.WriteAllText(Path.Combine(outDir, "report.txt"), report.ToString());
            return 0;
        }

        /// Brightness of what the page draws now: mean and max over a coarse grid (0-255).
        static async Task<string> Frame(CoreWebView2Controller c)
        {
            try
            {
                using (var ms = new MemoryStream())
                {
                    await c.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, ms);
                    ms.Position = 0;
                    using (var b = new Bitmap(ms))
                    {
                        double sum = 0; int max = 0, n = 0;
                        for (int y = b.Height / 40; y < b.Height; y += Math.Max(1, b.Height / 20))
                            for (int x = b.Width / 64; x < b.Width; x += Math.Max(1, b.Width / 32))
                            {
                                Color px = b.GetPixel(x, y);
                                int l = (px.R * 3 + px.G * 6 + px.B) / 10;
                                sum += l; max = Math.Max(max, l); n++;
                            }
                        return "mean=" + (int)(sum / n) + " max=" + max;
                    }
                }
            }
            catch (Exception e) { return "capture " + e.GetType().Name; }
        }

        static string SafeVersion()
        {
            try { Web.UseOurLoader(); return CoreWebView2Environment.GetAvailableBrowserVersionString(); }
            catch (Exception e) { return "error " + e.Message; }
        }
    }

    static class Native
    {
        [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
        [StructLayout(LayoutKind.Sequential)] public struct MSLLHOOKSTRUCT { public int x, y; public uint mouseData, flags, time; public IntPtr extra; }

        [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr value);

        // QueryDisplayConfig: which physical monitor (device path, EDID name) is behind each \\.\DISPLAYn
        [StructLayout(LayoutKind.Sequential)]
        public struct PATH
        {
            public uint sourceAdapterLow; public int sourceAdapterHigh; public uint sourceId, sourceModeIdx, sourceStatus;
            public uint targetAdapterLow; public int targetAdapterHigh; public uint targetId, targetModeIdx, outputTechnology, rotation, scaling,
                refreshNum, refreshDen, scanLineOrdering; public int targetAvailable; public uint targetStatus;
            public uint flags;
        }
        [StructLayout(LayoutKind.Sequential)]
        public struct MODE { public uint infoType, id, adapterLow; public int adapterHigh; public long u0, u1, u2, u3, u4, u5; }
        [StructLayout(LayoutKind.Sequential)]
        public struct HEADER { public uint type, size, adapterLow; public int adapterHigh; public uint id; }
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        public struct SOURCE_NAME { public HEADER header; [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string gdiName; }
        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        public struct TARGET_NAME
        {
            public HEADER header; public uint flags, outputTechnology; public ushort edidManufactureId, edidProductCodeId; public uint connectorInstance;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string friendlyName;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 128)] public string devicePath;
        }
        [DllImport("user32.dll")] public static extern int GetDisplayConfigBufferSizes(uint flags, out uint numPaths, out uint numModes);
        [DllImport("user32.dll")] public static extern int QueryDisplayConfig(uint flags, ref uint numPaths, [Out] PATH[] paths, ref uint numModes, [Out] MODE[] modes, IntPtr topology);
        [DllImport("user32.dll")] public static extern int DisplayConfigGetDeviceInfo(ref SOURCE_NAME info);
        [DllImport("user32.dll")] public static extern int DisplayConfigGetDeviceInfo(ref TARGET_NAME info);
        [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hWnd, int attr, ref int value, int size);

        public static void Cloak(IntPtr hWnd, bool on)
        {
            int v = on ? 1 : 0;
            try { DwmSetWindowAttribute(hWnd, 13, ref v, 4); } catch (Exception) { }  // DWMWA_CLOAK
        }
        [DllImport("user32.dll", SetLastError = true)] public static extern IntPtr SetWindowsHookEx(int idHook, Delegate fn, IntPtr mod, uint thread);
        [DllImport("user32.dll")] public static extern bool UnhookWindowsHookEx(IntPtr hook);
        [DllImport("user32.dll")] public static extern IntPtr CallNextHookEx(IntPtr hook, int code, IntPtr wParam, IntPtr lParam);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr GetModuleHandle(string name);
        [DllImport("user32.dll")] public static extern IntPtr SetParent(IntPtr child, IntPtr parent);
        [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr hWnd, int index);
        [DllImport("user32.dll")] public static extern int SetWindowLong(IntPtr hWnd, int index, int value);
        [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr hWnd, out RECT rect);
        [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr hWnd);
        [DllImport("user32.dll")] public static extern bool SystemParametersInfo(uint action, uint param, IntPtr pv, uint winIni);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] public static extern uint GetShortPathName(string path, StringBuilder sb, int size);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] public static extern bool DeleteFile(string path);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] public static extern uint GetLongPathName(string path, StringBuilder sb, int size);
    }
}

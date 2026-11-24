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

    static class Settings
    {
        const string Key = @"Software\ScreenMachine\Screensaver";

        public static string DataDir
        {
            get { return System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ScreenMachine", "Screensaver"); }
        }

        public static string Server
        {
            get { return (Get("Server") ?? Self.Trailer("server") ?? "").Trim().TrimEnd('/'); }
            set { Set("Server", (value ?? "").Trim().TrimEnd('/')); }
        }

        public static string Screen
        {
            get { return Get("Screen") ?? Self.Trailer("screen") ?? ""; }
            set { Set("Screen", value ?? ""); }
        }

        public static bool Ready { get { return Server != "" && Screen != ""; } }

        /// The TV's own page, as a viewer (never drives the screen), at full brightness (no room light mask).
        public static string DisplayUrl(string server, string screen)
        {
            return server + "/display/" + Uri.EscapeDataString(screen) + "?kiosk=viewer&nomask&nocolour";
        }

        static string Get(string name)
        {
            using (var k = Registry.CurrentUser.OpenSubKey(Key)) return k == null ? null : k.GetValue(name) as string;
        }

        static void Set(string name, string value)
        {
            using (var k = Registry.CurrentUser.CreateSubKey(Key)) k.SetValue(name, value);
        }

        public static void Forget()
        {
            Registry.CurrentUser.DeleteSubKeyTree(@"Software\ScreenMachine", false);
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
                var retry = new Timer { Interval = 30000 };
                retry.Tick += delegate { retry.Stop(); w.Navigate(url); };
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

    static class Saver
    {
        public static void Run()
        {
            string url = Settings.Ready ? Settings.DisplayUrl(Settings.Server, Settings.Screen) : null;
            Log.Write("saver " + (url ?? "(not set up)"));
            Input.Start();
            foreach (Screen s in Screen.AllScreens) new SaverForm(s.Bounds, url).Show();
            Cursor.Hide();
            Application.Run();
            Input.Stop();
            Environment.Exit(0);  // the browser processes go with us
        }
    }

    sealed class SaverForm : Form
    {
        readonly string url;

        public SaverForm(Rectangle bounds, string url)
        {
            this.url = url;
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
            if (url == null) return;
            try { await Web.Attach(this, url, true, "WebView2", null); }
            catch (Exception x) { Log.Write("webview: " + x.Message); }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            base.OnPaint(e);
            if (url != null) return;
            TextRenderer.DrawText(e.Graphics, "Choose a screen in the Screen Machine screensaver's settings.", new Font("Segoe UI", 14f),
                ClientRectangle, ForeColor, TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter);
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

        static void Quit(string why)
        {
            if ((DateTime.UtcNow - started).TotalMilliseconds < 800) return;  // the click on "Preview" and its release
            Log.Write("exit: " + why);
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
            if (!Settings.Ready) return;
            string server = Settings.Server, screen = Settings.Screen;
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
                // fill the little screen, cropping what overhangs (the TV page fills its screen the same way)
                float s = Math.Max((float)Width / picture.Width, (float)Height / picture.Height);
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

    /// The one window: which screen to show, and from where. Run from the download it installs; run from Windows'
    /// dialog ("Settings...") it saves. Both keep the screensaver set up the Windows way (Windows' dialog sets the wait
    /// and the lock).
    sealed class SettingsForm : Form
    {
        readonly ComboBox screens = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 340 };
        readonly TextBox server = new TextBox { Width = 340 };
        readonly Label note = new Label { AutoSize = true, MaximumSize = new Size(360, 0), ForeColor = Color.DimGray };
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
            col.Controls.Add(new Label { AutoSize = true, Text = "Show this screen when the computer is idle", Font = new Font("Segoe UI Semibold", 11f), Margin = new Padding(0, 0, 0, 8) });
            col.Controls.Add(screens);
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
            screens.SelectedIndexChanged += delegate { ShowAccess(); };
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

        void LoadScreens()
        {
            string from = server.Text.Trim().TrimEnd('/');
            string want = screens.SelectedItem is ScreenChoice ? ((ScreenChoice)screens.SelectedItem).Id : Settings.Screen;
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
                    screens.Items.Clear();
                    if (t.Result == null) { note.Text = "Couldn't reach " + from + "."; return; }
                    foreach (var s in t.Result)
                    {
                        screens.Items.Add(s);
                        if (s.Id == want) screens.SelectedItem = s;
                    }
                    if (screens.SelectedIndex < 0 && screens.Items.Count > 0) screens.SelectedIndex = 0;
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
            var s = screens.SelectedItem as ScreenChoice;
            bool open = s == null || s.Access == "guest";
            note.Text = open ? "Shows whatever that screen shows, live included." : "This screen is for signed-in people.";
            signIn.Visible = !open;
        }

        void Save()
        {
            var s = screens.SelectedItem as ScreenChoice;
            if (s == null) { MessageBox.Show(this, "Choose a screen first.", Text); return; }
            Settings.Server = server.Text;
            Settings.Screen = s.Id;
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
            report.AppendLine("server=" + Settings.Server + " screen=" + Settings.Screen + " trailer.server=" + Self.Trailer("server") + " trailer.screen=" + Self.Trailer("screen"));
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

            var thumb = PreviewForm.Thumbnail(Settings.Server, Settings.Screen);
            report.AppendLine("thumbnail=" + (thumb == null ? "none" : thumb.Width + "x" + thumb.Height));
            if (thumb != null) thumb.Save(Path.Combine(outDir, "preview.png"));

            string url = Settings.DisplayUrl(Settings.Server, Settings.Screen);
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
                    await Task.Delay(12000);
                    string state = await c.CoreWebView2.ExecuteScriptAsync(
                        "JSON.stringify({title:document.title,imgs:[...document.images].map(i=>i.currentSrc).slice(0,4)," +
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

// VORTEX: a small launch-pad window for the app and the folders around it. One
// shortcut instead of several: start the app (and open it in your browser), open
// the downloads folder, open or empty "Pending Deletion", and drive the two
// file flows (Rename, File) with a picker for anything that needs a human choice.
//
// Compiled, not a script: a security suite's behavioural engine watches for
// "something spawns a hidden process", and this window spawns a few things on
// click (the app, Explorer, Python).
//
// Settings come from an optional vortex.json (see vortex.json.example) next to
// the exe or in the project root; everything has a sensible default.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.VisualBasic.FileIO;

class Vortex
{
    // Its own space-themed palette. The accent colours mean the same thing
    // everywhere in the window:
    //   Primary = launch / go / primary action
    //   Safe    = safe / positive / create
    //   Danger  = destructive
    //   Muted   = view-only / informational
    internal static readonly Color PanelColor = ColorTranslator.FromHtml("#0A0E1A");
    internal static readonly Color TextColor = ColorTranslator.FromHtml("#D6F5EA");
    internal static readonly Color Primary = ColorTranslator.FromHtml("#38BDF8");  // blue
    internal static readonly Color Safe = ColorTranslator.FromHtml("#5DFFBA");     // green
    internal static readonly Color Danger = ColorTranslator.FromHtml("#E05A4E");   // red
    internal static readonly Color Muted = ColorTranslator.FromHtml("#9AA4B2");    // grey
    internal static readonly Color Purple = ColorTranslator.FromHtml("#B57CFF");
    internal static readonly Color Folder = ColorTranslator.FromHtml("#F2C14E");   // folder yellow
    internal static readonly Color Plain = ColorTranslator.FromHtml("#EDEFF3");    // white
    internal static readonly Color Orange = ColorTranslator.FromHtml("#FF9233");
    internal static readonly Color Teal = ColorTranslator.FromHtml("#2DD4BF");

    // One color per letter of the title (2026-08-23, at the user's
    // request) - purely decorative, not tied to any of the action colors
    // below.
    internal static readonly Color[] TitleLetterColors = new Color[] {
        ColorTranslator.FromHtml("#FF5A5A"), ColorTranslator.FromHtml("#FFA85A"),
        ColorTranslator.FromHtml("#FFE85A"), ColorTranslator.FromHtml("#5DFFBA"),
        ColorTranslator.FromHtml("#38BDF8"), ColorTranslator.FromHtml("#B57CFF"),
    };

    // Ring-only muted palette (2026-09-12, at the user's request) - the
    // portal's spinning rings use the same 6-colour palette as the
    // wordmark/sparks, but at full saturation and brightness on a 232px
    // ring it read as "blinding" rather than colourful. This is an
    // HSL-space adjustment of TitleLetterColors - saturation cut, then
    // gamma (darkens midtones more than shadows/highlights) then a small
    // contrast pass around the midpoint - kept as its own array so the
    // header wordmark and particle sparks (which nobody complained about)
    // stay at full intensity. DrawRing/DrawInnerRing use this instead of
    // TitleLetterColors directly.
    internal static readonly Color[] RingColors =
        BuildAdjustedPalette(TitleLetterColors, satMul: 0.62f, gamma: 1.35f, contrastMul: 1.12f);

    static float Clamp01(float v) { return v < 0f ? 0f : (v > 1f ? 1f : v); }

    static float HueToRgbComponent(float p, float q, float t)
    {
        if (t < 0f) t += 1f;
        if (t > 1f) t -= 1f;
        if (t < 1f / 6f) return p + (q - p) * 6f * t;
        if (t < 1f / 2f) return q;
        if (t < 2f / 3f) return p + (q - p) * (2f / 3f - t) * 6f;
        return p;
    }

    // h in [0,360), s/l in [0,1]. Color.GetHue/GetSaturation/GetBrightness
    // are already HSL (not HSB) under the hood, so this pairs with them.
    static Color HslToRgb(float h, float s, float l)
    {
        float r, g, b;
        if (s <= 0.0001f) { r = g = b = l; }
        else
        {
            float q = l < 0.5f ? l * (1f + s) : l + s - l * s;
            float p = 2f * l - q;
            float hn = h / 360f;
            r = HueToRgbComponent(p, q, hn + 1f / 3f);
            g = HueToRgbComponent(p, q, hn);
            b = HueToRgbComponent(p, q, hn - 1f / 3f);
        }
        return Color.FromArgb((int)Math.Round(Clamp01(r) * 255f),
                               (int)Math.Round(Clamp01(g) * 255f),
                               (int)Math.Round(Clamp01(b) * 255f));
    }

    static Color AdjustHsl(Color c, float satMul, float gamma, float contrastMul)
    {
        float h = c.GetHue();
        float s = Clamp01(c.GetSaturation() * satMul);
        float l = Clamp01((float)Math.Pow(c.GetBrightness(), gamma));
        l = Clamp01(0.5f + (l - 0.5f) * contrastMul);
        return HslToRgb(h, s, l);
    }

    static Color[] BuildAdjustedPalette(Color[] src, float satMul, float gamma, float contrastMul)
    {
        var out_ = new Color[src.Length];
        for (int i = 0; i < src.Length; i++) out_[i] = AdjustHsl(src[i], satMul, gamma, contrastMul);
        return out_;
    }

    // ------------------------------------------------------------------
    //  FX - the launch-pad "cool factor" layer (2026-09-07, v2.11).
    //  Every effect is an independent switch: flip its bool to false to
    //  kill just that one, nothing else changes. The animated state below
    //  is written once per frame by the form's 60fps timer (BuildForm)
    //  and read by the various OnPaint overrides.
    // ------------------------------------------------------------------
    internal static class Fx
    {
        internal const bool Tunnel       = true;  // slow vortex rings turning inside the portal
        internal const bool ReactiveRing = true;  // portal ring spins up on hover, flares white on launch
        internal const bool SceneTint    = true;  // fixed cool scene tint (no time-of-day drift - see HubForm.OnPaintBackground)
        internal const bool WordmarkWarp = true;  // Ctrl+Shift+V -> full vortex warp burst
        internal const bool IntroSettle  = false; // one simultaneous micro-bounce on open (false = just appear)

        internal static float Clock;         // seconds since the first tick
        internal static float PortalPull;    // 0..1 eased "a launch button is hovered"
        internal static float PortalCharge;  // 0..1 decaying "a launch just fired"
        internal static float Warp;          // 0..1 decaying warp burst
    }

    // Running version. Bump it whenever Vortex.cs changes.
    const string Version = "3.0.0";

    // Settings (vortex.json, all optional). PythonExe is a full path or null.
    static string PythonExe;
    static string AppName = "Entertainment and Talk Over It";
    static int AppPort = 5183;
    static string DownloadsDir;

    static string FindProjectRoot(string start)
    {
        for (string d = start; !string.IsNullOrEmpty(d); d = Path.GetDirectoryName(d))
            if (File.Exists(Path.Combine(d, "run.py"))) return d;
        return Path.GetDirectoryName(start);
    }

    static string ResolvePython(string configured)
    {
        if (!string.IsNullOrEmpty(configured) && File.Exists(configured)) return configured;
        string venv = Path.Combine(ProjectRoot, ".venv", "Scripts", "python.exe");
        if (File.Exists(venv)) return venv;
        try
        {
            var psi = new ProcessStartInfo("where", "python")
            {
                RedirectStandardOutput = true, UseShellExecute = false, CreateNoWindow = true,
            };
            using (var proc = Process.Start(psi))
            {
                string output = proc.StandardOutput.ReadToEnd();
                proc.WaitForExit(3000);
                foreach (string line in output.Split('\n'))
                {
                    string p = line.Trim();
                    // skip the Microsoft Store stub in WindowsApps, it is not a real interpreter
                    if (p.Length > 0 && File.Exists(p) && p.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) < 0) return p;
                }
            }
        }
        catch { }
        return null;
    }

    static void LoadConfig()
    {
        Dictionary<string, object> cfg = new Dictionary<string, object>();
        foreach (string dir in new[] { AutomationDir, ProjectRoot })
        {
            string path = Path.Combine(dir, "vortex.json");
            if (!File.Exists(path)) continue;
            try { cfg = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(path)); break; }
            catch { }
        }
        Func<string, string> str = k => cfg.ContainsKey(k) && cfg[k] != null ? cfg[k].ToString() : "";
        if (str("app_name").Length > 0) AppName = str("app_name");
        int port;
        if (int.TryParse(str("app_port"), out port) && port > 0) AppPort = port;
        PythonExe = ResolvePython(str("python_exe"));
        DownloadsDir = str("downloads_dir");
        if (DownloadsDir.Length == 0) DownloadsDir = FirstIncomingFolder();
        if (DownloadsDir.Length == 0)
            DownloadsDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Downloads");
    }

    // The first incoming folder in the app's own config.json, if it has one.
    static string FirstIncomingFolder()
    {
        try
        {
            string path = Path.Combine(ProjectRoot, "config.json");
            if (!File.Exists(path)) return "";
            var cfg = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
            var roots = cfg.ContainsKey("queue_roots") ? cfg["queue_roots"] as System.Collections.ArrayList : null;
            if (roots == null || roots.Count == 0) return "";
            var first = roots[0] as Dictionary<string, object>;
            if (first != null && first.ContainsKey("path")) return first["path"].ToString();
            return roots[0] as string ?? "";
        }
        catch { return ""; }
    }

    // Bahnschrift - the DIN-style technical / "control-systems" family
    // (confirmed installed 2026-08-27), replacing Agency FB: same condensed
    // footprint, but modern where Agency FB read late-90s. "Bahnschrift
    // SemiBold" is its own installed family name, so weight comes from
    // picking that family, not from synth-bolding. Falls back
    // Bahnschrift -> Segoe UI -> generic sans so a missing font is never a
    // startup crash. `new FontFamily(name)` throws on a missing family
    // (unlike `new Font(name, ...)`, which silently substitutes), so it's
    // the real installed-or-not test.
    internal static Font SpaceFont(float size, FontStyle style)
    {
        bool strong = (style & FontStyle.Bold) != 0;
        FontStyle rest = style & ~FontStyle.Bold;
        string[] families = strong
            ? new[] { "Bahnschrift SemiBold", "Bahnschrift", "Segoe UI Semibold", "Segoe UI" }
            : new[] { "Bahnschrift", "Segoe UI", "Segoe UI" };
        foreach (var fam in families)
        {
            try { using (var ff = new FontFamily(fam)) return new Font(ff, size, rest); }
            catch { }
        }
        return new Font(FontFamily.GenericSansSerif, size, style);
    }

    static string ProjectRoot;
    static string AutomationDir;
    static string AssetsDir;

    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);

        AutomationDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
        ProjectRoot = FindProjectRoot(AutomationDir);
        AssetsDir = Path.Combine(AutomationDir, "Assets");
        LoadConfig();

        var form = BuildForm();
        Application.Run(form);
    }

    static Form BuildForm()
    {
        var form = new HubForm
        {
            Text = "Vortex",
            // Borderless (2026-08-27) - the window draws its own chrome:
            // drag by the top strip / wordmark, custom minimise + close
            // glyphs top-right, a 1px edge and a drop shadow (HubForm).
            FormBorderStyle = FormBorderStyle.None,
            // Positioned by hand below (Manual) rather than CenterScreen:
            // CenterScreen centres on the full screen *bounds*, ignoring the
            // taskbar and - worse - display scaling. On a laptop at 125/150%
            // a 970px-tall window is taller than the working area, so
            // centring pushed its top edge off-screen and clipped the
            // header. The manual placement below centres within the working
            // area and clamps the top so the header is always fully visible.
            StartPosition = FormStartPosition.Manual,
            // 1120x970 - grown 30px to match the whole bottom cluster
            // (tiles, action row, the companion tool) moving down 30px. Fixed
            // size, centred.
            ClientSize = new Size(1120, 970),
            BackColor = Color.Black,
        };
        {
            Rectangle wa = Screen.FromPoint(Cursor.Position).WorkingArea;
            int fw = form.Width, fh = form.Height;
            int x = wa.Left + (wa.Width - fw) / 2;
            int y = wa.Top + (wa.Height - fh) / 2;
            // Never let the top edge go above the working area - if the
            // window is taller than the screen fits, we'd rather lose a
            // little off the bottom (draggable, and F11 toggles fullscreen)
            // than clip the wordmark.
            if (x < wa.Left) x = wa.Left;
            if (y < wa.Top) y = wa.Top;
            form.Location = new Point(x, y);
        }
        // Batches every Controls.Add below into one layout pass instead of
        // one per control - cuts down on construction-time flicker.
        form.SuspendLayout();
        string vortexIconPath = Path.Combine(AssetsDir, "vortex_hub.ico");
        if (File.Exists(vortexIconPath)) form.Icon = new Icon(vortexIconPath);

        // All images/icons live under automation\Assets - copied in from
        // wherever they originally came from (the Desktop, Documents\Icons)
        // 2026-08-22, so Vortex is self-contained and doesn't break if any
        // of those original files/folders ever move or get cleaned up.
        // Stretch, not Zoom (2026-08-23, per request) - Zoom preserved
        // aspect ratio but letterboxed top/bottom, leaving flat black bars;
        // Stretch fills the window completely top-to-bottom, at the cost of
        // some minor distortion on a window this shape, which reads better
        // for a background than visible letterboxing does.
        // VORTEX2.png (2026-09-12) replaces the old time-vortex background -
        // it's a native 1120x970 match for ClientSize so Stretch is a no-op
        // here. A flat darken (RGB x0.612) read as dull rather than
        // moody per the user - darks weren't dark and lights didn't pop.
        // Switched to a brightness-then-contrast pass instead: v_out =
        // clamp((v*B - 0.5)*1.4 + 0.5) - dims first, then stretches around
        // the midpoint so shadows push toward black and highlights push
        // toward white instead of everything moving the same amount.
        // B nudged 0.75 -> 0.82 (2026-09-18, "a little brighter" overall) -
        // contrast left at 1.4, unchanged, so the darks-stay-dark /
        // lights-pop character isn't diluted, just the overall level.
        string bgPath = Path.Combine(AssetsDir, "VORTEX2.png");
        if (File.Exists(bgPath))
        {
            form.BackgroundImage = Image.FromFile(bgPath);
            form.BackgroundImageLayout = ImageLayout.Stretch;
        }

        // "VORTEX" painted letter-by-letter in different colors, over a
        // dimmed backdrop panel sized to the text itself (not the whole
        // header strip) - a true blur of the moving background image isn't
        // practical for a fixed GDI+-painted title, so this dims instead:
        // same practical effect (legible text over a busy image).
        // EXPERIMENT (2026-09-13) - title moved from top/center into the
        // top-right, per the user's marked-up screenshot. DrawRainbowTitle
        // centres its content within whatever bounds this panel has, so
        // this is just a relocated/narrowed box: below the window control
        // glyphs (y<38), right of centre, clear of the portal (top 280).
        var titlePanel = new Panel
        {
            Location = new Point(830, 34),
            Size = new Size(270, 150),
            BackColor = Color.Transparent,
        };
        titlePanel.Paint += (sender, e) => DrawRainbowTitle(e.Graphics, titlePanel.ClientRectangle);
        form.Controls.Add(titlePanel);

        // Borderless window: drag by any empty area or by the wordmark strip.
        MakeDraggable(form, form);
        MakeDraggable(titlePanel, form);

        // The app itself - the prominent element, icon only, no text, no
        // boxed panel around it (unlike the three utility buttons below) -
        // it floats directly over the background, which is what actually
        // sets it apart as "the one that matters" rather than a label
        // saying so.
        // 320x320 control holding a 200px art centred in it - the extra
        // margin all round is what lets the full neon ring + its glow
        // render without the control's own rectangle clipping it into a
        // box (the ring radius is fixed in DrawRing, sized to the art, NOT
        // to this control). Centred in the 1120-wide window.
        // Moved down from y=154 -> 226 -> 280 (2026-09-12, at the user's
        // request, in two passes). Bottom now sits at 600, still 42px
        // clear of the utility-tile row (top 642).
        var portalBtn = new PortalButton
        {
            Location = new Point(400, 280),
            Size = new Size(320, 320),
        };
        // Vortex-portal art (2026-08-22) - the "launch" action. Loaded from
        // the raw PNG via LoadScaledBitmap(), not new Icon(path, w, h) -
        // that constructor renders PNG-compressed icon frames as solid
        // noise (a confirmed .NET Framework GDI+ bug).
        string portalArtPath = Path.Combine(AssetsDir, "Vortex.png");
        if (File.Exists(portalArtPath))
            portalBtn.Art = LoadScaledBitmap(portalArtPath, 200);
        portalBtn.Click += (sender, e) => SafeInvoke(LaunchApp);
        form.Controls.Add(portalBtn);

        // Two supporting shortcuts, same size, in a row underneath.
        string pendingDeletionDir = Path.Combine(ProjectRoot, "Pending Deletion");
        string downloadsDir = DownloadsDir;

        // 190 wide (up from 150) so "Pending Deletion" fits on one line
        // without wrapping; 110 tall (down from 150) per the "too tall" ask.
        // Whole layout scaled ~1.25x alongside the window itself
        // (2026-08-23) - same proportions as before, just bigger.
        // Two tiles centred in the 1120-wide window: 2*300 + 28 = 628;
        // (1120 - 628) / 2 = 246 margin each side.
        const int boxW = 300, boxH = 138, boxY = 700, margin = 246, gap = 28;
        int box1X = margin, box2X = margin + boxW + gap;

        // Per-button colours are the user's aesthetic choice (2026-08-28),
        // not the earlier semantic grammar: Downloads green, Pending
        // Deletion purple, Run Backup blue. All three share the same
        // icon-above-text shape now (Run Backup no longer a shell32 icon).
        var downloadsBtn = MakeButton(
            new Point(box1X, boxY), new Size(boxW, boxH),
            "Downloads", GlyphKind.FolderDown, 54, Safe,
            () => OpenFolder(downloadsDir));

        var pendingBtn = MakeButton(
            new Point(box2X, boxY), new Size(boxW, boxH),
            "Pending Deletion", GlyphKind.Trash, 54, Purple,
            () => OpenFolder(pendingDeletionDir));

        form.Controls.Add(downloadsBtn);
        form.Controls.Add(pendingBtn);

        // Small, separate action under Pending Deletion - sends everything
        // in that folder to the Recycle Bin (not a permanent delete: fully
        // undoable from there afterward), completing the move-then-review
        // workflow the main app's Purge button already starts. Kept as its
        // own button rather than folded into opening the folder, since
        // "look at it" and "get rid of it" are two different actions that
        // deserve two different clicks - no accidental deletions from a
        // single misclick on the folder tile.
        int actionBtnY = boxY + boxH + 15;
        var recycleBtn = MakeActionButton(
            new Point(box2X, actionBtnY), boxW, Danger,
            "Empty to Recycle Bin", GlyphKind.Recycle, btn => EmptyPendingDeletion(btn, pendingDeletionDir));
        form.Controls.Add(recycleBtn);

        // Two actions under Downloads, side by side. "Rename" applies the
        // TMDB-verified naming convention to files still in the queue,
        // in place (pipeline/rename_queue_inplace.py) - never moves or
        // removes anything. "File" moves them into their real E:\Video
        // archive folder (pending a the app entry point - see FileDownloads).
        const int actGap = 8;
        int actHalf = (boxW - actGap) / 2;
        var renameBtn = MakeActionButton(
            new Point(box1X, actionBtnY), actHalf, Primary,
            "Rename", GlyphKind.Pencil, RenameQueueInPlace);
        var fileBtn = MakeActionButton(
            new Point(box1X + actHalf + actGap, actionBtnY), actHalf, Folder,
            "File", GlyphKind.FolderIn, FileDownloads);
        form.Controls.Add(renameBtn);
        form.Controls.Add(fileBtn);

        // Dim build tag, bottom-right - a small "this is real software" cue.
        // Monospace (Consolas, generic-mono fallback), very low-contrast so
        // it reads only when looked for.
        Font tagFont;
        try { using (var ff = new FontFamily("Consolas")) tagFont = new Font(ff, 8f); }
        catch { tagFont = new Font(FontFamily.GenericMonospace, 8f); }
        // Build date is stamped automatically from Vortex.exe's own file
        // timestamp - no more hand-editing a literal each rebuild.
        string buildDate;
        try { buildDate = File.GetLastWriteTime(System.Reflection.Assembly.GetExecutingAssembly().Location).ToString("yyyy.MM.dd"); }
        catch { buildDate = DateTime.Now.ToString("yyyy.MM.dd"); }
        var buildTag = new Label
        {
            Text = "VORTEX  v" + Version + "  ·  " + buildDate,
            AutoSize = true,
            BackColor = Color.Transparent,
            ForeColor = Color.FromArgb(110, 154, 164, 178),
            Font = tagFont,
        };
        form.Controls.Add(buildTag);
        buildTag.Location = new Point(
            form.ClientSize.Width - buildTag.PreferredWidth - 14,
            form.ClientSize.Height - buildTag.PreferredHeight - 10);
        buildTag.BringToFront();

        // Custom window buttons (borderless): minimise, fullscreen, close -
        // top-right. Fullscreen is an experiment the user asked to see: a
        // borderless form's Maximized state covers the whole screen
        // (including over the taskbar), so this genuinely goes full-screen
        // rather than just "big." Known limitation: nothing re-lays-out for
        // the larger canvas - the background image stretches to fill, but
        // every button/portal/panel stays at its normal-size position and
        // dimensions, so there's empty background around them rather than
        // a rescaled layout. F11 toggles it too.
        var closeBtn = new GlyphButton
        {
            Type = GlyphButton.Kind.Close,
            Location = new Point(form.ClientSize.Width - 38, 6),
        };
        closeBtn.Click += (s, e) => form.Close();
        var maxBtn = new GlyphButton
        {
            Type = GlyphButton.Kind.Maximize,
            Location = new Point(form.ClientSize.Width - 74, 6),
        };
        Action toggleFullscreen = () => form.WindowState =
            form.WindowState == FormWindowState.Maximized ? FormWindowState.Normal : FormWindowState.Maximized;
        maxBtn.Click += (s, e) => toggleFullscreen();
        var minBtn = new GlyphButton
        {
            Type = GlyphButton.Kind.Minimize,
            Location = new Point(form.ClientSize.Width - 110, 6),
        };
        minBtn.Click += (s, e) => form.WindowState = FormWindowState.Minimized;
        form.Controls.Add(closeBtn);
        form.Controls.Add(maxBtn);
        form.Controls.Add(minBtn);
        closeBtn.BringToFront();
        maxBtn.BringToFront();
        minBtn.BringToFront();

        form.KeyPreview = true;
        form.KeyDown += (s, e) => { if (e.KeyCode == Keys.F11) toggleFullscreen(); };

        // Aware-launcher status: one quiet strip bottom-left (mirroring the
        // build tag bottom-right), instead of pills/dots crowding the
        // buttons. Two up/down dots + whatever counts are non-zero.
        var statusStrip = new StatusStrip { Location = new Point(22, form.ClientSize.Height - 32) };
        form.Controls.Add(statusStrip);
        statusStrip.BringToFront();

        // Quiet caption centred between the action row and the status strip:
        // empty until a background job runs.
        Caption = new CaptionLabel { Location = new Point(110, 907) };
        form.Controls.Add(Caption);

        form.Shown += (s, e) => RefreshStatusAsync(statusStrip);
        form.Activated += (s, e) =>
        {
            if ((DateTime.UtcNow - _lastStatusUtc).TotalSeconds > 3) RefreshStatusAsync(statusStrip);
        };

        // ---- one shared 60fps timer: press feedback + the whole FX layer --
        // Buttons load at once now (the old staggered rise read as glitchy).
        // The timer drives: per-button press-dip easing, the portal's
        // reactive spin + launch flare, the animated background (tunnel /
        // spotlight / perimeter / time tint) via a single form.Invalidate(),
        // and the occasional kicker glitch. Paused while the window isn't
        // active, so it costs nothing in the background.
        var hubs = form.Controls.OfType<HubButton>().ToArray();
        // IntroSettle is `const false` right now (see Fx) - the compiler
        // correctly flags this body as unreachable (CS0162). Suppressed
        // explicitly rather than left as an unexplained warning on every
        // build, so a genuinely new CS0162 elsewhere doesn't get lost in
        // "oh that's just the usual one."
#pragma warning disable 0162
        if (Vortex.Fx.IntroSettle)
            foreach (var h in hubs) h.Anim = 1f;   // one simultaneous settle, no per-button delay
#pragma warning restore 0162

        // launch buttons feed the portal: hovering any pulls the ring's
        // spin up; clicking any fires a charge flare.
        portalBtn.Click     += (s, e) => Vortex.Fx.PortalCharge = 1f;
        form.KeyDown += (s, e) =>
        {
            if (Vortex.Fx.WordmarkWarp && e.Control && e.Shift && e.KeyCode == Keys.V)
            {
                Vortex.Fx.Warp = 1f;
                if (Vortex.Fx.PortalCharge < 0.6f) Vortex.Fx.PortalCharge = 0.6f;
            }
        };

        DateTime animStart = DateTime.MinValue, lastTick = DateTime.Now;
        float spin = 8f;
        var anim = new Timer { Interval = 16 };
        anim.Tick += (s, e) =>
        {
            var now = DateTime.Now;
            if (animStart == DateTime.MinValue) animStart = now;
            float el = (float)(now - animStart).TotalSeconds;
            float dt = (float)(now - lastTick).TotalSeconds;
            if (dt > 0.1f) dt = 0.1f;            // don't lurch after the window was inactive
            lastTick = now;
            Vortex.Fx.Clock = el;

            foreach (var h in hubs) h.Step(el, dt);

            // portal reactive spin + decaying flares
            float pullTarget = portalBtn.Hovered ? 1f : 0f;
            Vortex.Fx.PortalPull += (pullTarget - Vortex.Fx.PortalPull) * Math.Min(1f, dt * 6f);
            Vortex.Fx.PortalCharge *= (float)Math.Pow(0.015, dt);
            if (Vortex.Fx.PortalCharge < 0.004f) Vortex.Fx.PortalCharge = 0f;
            Vortex.Fx.Warp *= (float)Math.Pow(0.30, dt);
            if (Vortex.Fx.Warp < 0.004f) Vortex.Fx.Warp = 0f;

            float targetSpin = Vortex.Fx.ReactiveRing
                ? 8f + 46f * Vortex.Fx.PortalPull + 190f * Vortex.Fx.PortalCharge + 260f * Vortex.Fx.Warp
                : 8f;
            spin += (targetSpin - spin) * Math.Min(1f, dt * 4f);
            portalBtn.Angle = (portalBtn.Angle + dt * spin) % 360f;
            portalBtn.Invalidate();

            // NOTE: no per-frame form.Invalidate(). Repainting the whole
            // window every frame made the (single-buffered) header Panel
            // redraw its rainbow text 60x/sec = unbearable flicker. All the
            // *ambient* motion now lives inside the portal control, which
            // already repaints itself each frame with no flicker.
        };
        form.Activated += (s, e) => { lastTick = DateTime.Now; anim.Start(); };
        form.Deactivate += (s, e) => anim.Stop();
        anim.Start();

        // Keep the UI centred when the window is bigger than the layout it
        // was drawn for (i.e. fullscreen / maximised). Every control is
        // positioned in fixed pixels against a 1120x970 canvas; without
        // this they all stay pinned to the top-left and the rest of a wide
        // screen is dead space. On resize, shift every control by half the
        // slack in each axis (never negative - a smaller screen still just
        // clips from the right/bottom as before). Controls are NOT
        // reparented - they stay direct children of the form so their
        // transparent-corner InvokePaintBackground keeps sampling the
        // stretched background image correctly.
        const int designW = 1120, designH = 970;
        var homeLoc = new System.Collections.Generic.Dictionary<Control, Point>();
        foreach (Control c in form.Controls) homeLoc[c] = c.Location;
        EventHandler recentre = delegate
        {
            int dx = Math.Max(0, (form.ClientSize.Width - designW) / 2);
            int dy = Math.Max(0, (form.ClientSize.Height - designH) / 2);
            foreach (var kv in homeLoc)
                kv.Key.Location = new Point(kv.Value.X + dx, kv.Value.Y + dy);
        };
        form.Resize += recentre;
        recentre(form, EventArgs.Empty);

        form.ResumeLayout(true);
        return form;
    }

    // Uniform layout for the three utility buttons - icon centered on top,
    // text centered below, boxed in a dark panel so they stay legible over
    // a busy space photo. Deliberately simple rather than mixing
    // alignments/fonts per line: this can't be visually iterated on (no
    // way to screenshot a native Windows window in this environment), so a
    // plain, predictable layout that can't render wrong beats a fancier
    // one that might.
    static HubButton MakeButton(Point location, Size size, string text, GlyphKind glyph, int iconSize, Color accent, Action onClick)
    {
        var btn = new HubButton
        {
            Location = location,
            Size = size,
            ForeColor = accent,
            Accent = accent,
            Radius = 12,
            Mode = HubButton.ContentLayout.IconAbove,
            Font = SpaceFont(19F, FontStyle.Bold),
            Text = text,
        };
        if (glyph != GlyphKind.None)
            btn.Image = GlyphBitmap(glyph, iconSize, accent);
        btn.Click += (sender, e) => SafeInvoke(onClick);
        return btn;
    }

    // Small text-only action button under one of the three utility boxes -
    // same shape "Empty to Recycle Bin" already used, now shared by all
    // three so their border/hover behavior can never drift apart. onClick
    // receives the button itself (via the event's own `sender`, not
    // Form.ActiveForm.ActiveControl - the latter is not reliably the
    // clicked control) so a slow action like RenameQueueInPlace can update
    // its own text/enabled state while it runs.
    static Button MakeActionButton(Point location, int width, Color color, string text, GlyphKind glyph, Action<Button> onClick)
    {
        var btn = new HubButton
        {
            Location = location,
            Size = new Size(width, 38),
            ForeColor = color,
            Accent = color,
            Radius = 9,
            Mode = glyph == GlyphKind.None ? HubButton.ContentLayout.TextOnly : HubButton.ContentLayout.IconLeft,
            Font = SpaceFont(14F, FontStyle.Bold),
            Text = text,
        };
        if (glyph != GlyphKind.None)
            btn.Image = GlyphBitmap(glyph, 16, color);
        btn.Click += (sender, e) => SafeInvoke(() => onClick((Button)sender));
        return btn;
    }

    // Header, two distinct pieces:
    //   "VORTEX"       - the bold statement: 54pt, fake-bolded, per-letter
    //                    rainbow + glow, in a snug dark rounded backdrop
    //                    sized to the word alone.
    //   "MEDIA CONTROL" - a smaller, boxless kicker below it in a different
    //                    style: wide-tracked caps, muted, flanked by short
    //                    rules, with a soft shadow so it reads on its own.
    static void DrawRainbowTitle(Graphics g, Rectangle bounds)
    {
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAlias;

        const string word = "VORTEX";
        const float track = 1.0f;

        using (var wordFont = SpaceFont(42F, FontStyle.Bold))
        using (var tagFont = SpaceFont(10.5F, FontStyle.Regular))
        {
            var adv = new float[word.Length];
            float wordW = 0f;
            for (int i = 0; i < word.Length; i++)
            {
                adv[i] = g.MeasureString(word[i].ToString(), wordFont, int.MaxValue, StringFormat.GenericTypographic).Width * track;
                wordW += adv[i];
            }
            float wordH = wordFont.GetHeight(g);

            float cx = bounds.Left + bounds.Width / 2f;
            float wordX = cx - wordW / 2f;
            // Generous clearance from the panel's own top edge - the panel
            // sits only 2px from the window's top, and the glow/fake-bold
            // stamps draw a couple of px above wordY.
            float wordY = bounds.Top + 24f;

            // Snug backdrop hugging VORTEX only - brought back 2026-09-12
            // (removed in v2.14.8 when the letters were dimmed instead, but
            // between that dimming and the background's own contrast pass
            // getting stronger since, the wordmark lost legibility - the
            // box is what actually keeps it readable over a busy image).
            const float pad = 18f;
            float bx = cx - wordW / 2f - pad;
            float bTop = wordY + wordH * 0.07f;
            float bBot = wordY + wordH * 0.87f;
            using (var backdrop = new SolidBrush(Color.FromArgb(135, 10, 10, 20)))
            using (var bp = RoundedRectPath(new RectangleF(bx, bTop, (cx + wordW / 2f + pad) - bx, bBot - bTop), 12))
                g.FillPath(backdrop, bp);

            int[] ring = { -2, 0, 2 };
            float x = wordX;
            for (int i = 0; i < word.Length; i++)
            {
                // Back to full TitleLetterColors brightness now that the
                // backdrop is doing the legibility work again - the gamma-
                // dimmed TitleWordColors variant (v2.14.8) only made sense
                // without a box behind it.
                Color c = TitleLetterColors[i % TitleLetterColors.Length];
                string ch = word[i].ToString();
                using (var glow = new SolidBrush(Color.FromArgb(26, c)))
                    foreach (int gx in ring)
                        foreach (int gy in ring)
                            if (gx != 0 || gy != 0)
                                g.DrawString(ch, wordFont, glow, x + gx, wordY + gy, StringFormat.GenericTypographic);
                using (var brush = new SolidBrush(c))
                {
                    g.DrawString(ch, wordFont, brush, x, wordY, StringFormat.GenericTypographic);
                    g.DrawString(ch, wordFont, brush, x + 0.9f, wordY, StringFormat.GenericTypographic);
                    g.DrawString(ch, wordFont, brush, x, wordY + 0.9f, StringFormat.GenericTypographic);
                }
                x += adv[i];
            }

            const string tag = "MEDIA CONTROL";
            const float tagGap = 9f;
            float tagW = TrackedWidth(g, tag, tagFont, tagGap);
            float tagY = bBot + 10f;
            float tagMid = tagY + tagFont.GetHeight(g) / 2f;

            using (var rule = new Pen(Color.FromArgb(115, Muted), 1f))
            {
                g.DrawLine(rule, cx - tagW / 2f - 48f, tagMid, cx - tagW / 2f - 14f, tagMid);
                g.DrawLine(rule, cx + tagW / 2f + 14f, tagMid, cx + tagW / 2f + 48f, tagMid);
            }
            // TextColor (the app's actual bright text color), not a
            // lightened Muted grey (2026-09-13, at the user's request -
            // "find a better color" rather than just brightening grey).
            DrawTrackedString(g, tag, tagFont, Color.FromArgb(170, 0, 0, 6), cx - tagW / 2f + 1.2f, tagY + 1.2f, tagGap);
            DrawTrackedString(g, tag, tagFont, Color.FromArgb(255, TextColor), cx - tagW / 2f, tagY, tagGap);
        }
    }

    // Per-character advance. GenericTypographic reports ~0 width for a
    // space (it strips whitespace advance), which collapsed the gap in
    // "MEDIA CONTROL" - so force a real width for the space.
    static float CharAdv(Graphics g, char c, Font f)
    {
        if (c == ' ') return f.SizeInPoints * 0.95f;
        return g.MeasureString(c.ToString(), f, int.MaxValue, StringFormat.GenericTypographic).Width;
    }

    static float TrackedWidth(Graphics g, string s, Font f, float gap)
    {
        float w = 0f;
        foreach (char c in s) w += CharAdv(g, c, f) + gap;
        return w - gap;
    }

    static void DrawTrackedString(Graphics g, string s, Font f, Color color, float x, float y, float gap)
    {
        using (var brush = new SolidBrush(color))
            foreach (char c in s)
            {
                if (c != ' ') g.DrawString(c.ToString(), f, brush, x, y, StringFormat.GenericTypographic);
                x += CharAdv(g, c, f) + gap;
            }
    }

    internal static System.Drawing.Drawing2D.GraphicsPath RoundedRectPath(RectangleF r, float radius)
    {
        float d = radius * 2;
        var path = new System.Drawing.Drawing2D.GraphicsPath();
        path.AddArc(r.X, r.Y, d, d, 180, 90);
        path.AddArc(r.Right - d, r.Y, d, d, 270, 90);
        path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        path.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }

    // Runs a Python script OFF the UI thread so the launch pad never
    // freezes - you can start Rename/File and keep using VORTEX. While it
    // runs the button shows the little page-into-folder loop (HubButton)
    // or, for a plain Button, the `busy` text. `done(exit, stdout, stderr)`
    // is marshalled back to the UI thread when it finishes. If VORTEX is
    // closed mid-run the child (its own PID) still completes; only the
    // result popup is lost. `done` may itself start another RunBackground
    // (that's how File chains --plan -> dialog -> --execute).
    // ---- caption: one quiet line, centred under the buttons, saying what a background job is
    // doing while the button animates exactly as before, then the outcome for a few seconds. ----
    static CaptionLabel Caption;
    static void Say(string text) { if (Caption != null) Caption.Working(text); }
    static void Outcome(string text) { if (Caption != null) Caption.Result(text, 6); }
    static string DefaultCaption(string flow, string busy)
    {
        switch (busy)
        {
            case "Planning...": return flow + " \u00b7 checking the download queue\u2026";
            case "Refreshing library...": return flow + " \u00b7 library data is behind the queue, refreshing it first (about 1 to 2 minutes)\u2026";
            case "Filing...": return flow + " \u00b7 moving your files into the library\u2026";
            case "Renaming...": return flow + " \u00b7 renaming files in place\u2026";
            default: return flow + " \u00b7 working\u2026";
        }
    }

    static void RunBackground(Button btn, string busy, string arguments, Action<int, string, string> done, string caption = null)
    {
        string original = btn.Text;
        Say(caption ?? DefaultCaption(original, busy));
        var hub = btn as HubButton;
        if (hub != null) hub.Busy = true; else btn.Text = busy;
        btn.Enabled = false;

        var th = new System.Threading.Thread(() =>
        {
            int code = -999; string so = "", se = "";
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = PythonExe,
                    Arguments = arguments,
                    WorkingDirectory = ProjectRoot,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                };
                using (var proc = Process.Start(psi))
                {
                    so = proc.StandardOutput.ReadToEnd();
                    se = proc.StandardError.ReadToEnd();
                    proc.WaitForExit();
                    code = proc.ExitCode;
                }
            }
            catch (Exception ex) { se = ex.Message; }

            int fc = code; string fso = so, fse = se;
            try
            {
                if (!btn.IsDisposed)
                    btn.BeginInvoke((Action)(() =>
                    {
                        if (hub != null) hub.Busy = false; else btn.Text = original;
                        btn.Enabled = true;
                        if (Caption != null) Caption.Clear();   // stage finished; done() may start the next one
                        done(fc, fso, fse);
                    }));
            }
            catch { }
        });
        th.IsBackground = true;
        th.Start();
    }

    // "Rename" - apply the TMDB-verified naming convention to files still in
    // the download queue, in place; never moves or removes anything. Drives
    // The app's rename_queue_inplace.py: `--plan` (background) -> a
    // confirm/pick popup -> `--apply <selection>` (background). The popup is
    // the point: a reboot/dual-entry show (Kitchen Nightmares) can't be
    // renamed on a silent guess any more - its readings are offered as
    // alternatives the user picks from. Both Python runs are off the UI
    // thread; only the modal picker blocks.
    static void RenameQueueInPlace(Button btn)
    {
        string scriptPath = Path.Combine(ProjectRoot, "pipeline", "rename_queue_inplace.py");
        if (!File.Exists(PythonExe) || !File.Exists(scriptPath))
        {
            HubDialog.Show(HubDialog.Kind.Error, "Rename", "Couldn't find Python or the rename script.");
            return;
        }
        string q = "\"" + scriptPath + "\" ";

        RunBackground(btn, "Planning...", q + "--plan", (code, so, se) =>
        {
            RenamePlan plan = ReadRenamePlan();
            if (plan == null)
            {
                if (code != 0)
                    HubDialog.Show(HubDialog.Kind.Error, "Rename",
                        "Couldn't build the rename plan.  " + FirstLine(se.Length > 0 ? se : so));
                return;
            }

            var ready = plan.ready ?? new RenamePlanItem[0];
            var review = plan.needs_review ?? new RenamePlanItem[0];
            if (ready.Length == 0 && review.Length == 0)
            {
                HubDialog.Show(HubDialog.Kind.Info, "Rename", "Nothing in the download queue to rename right now.");
                Outcome("Rename \u00b7 nothing to rename right now");
                return;
            }

            List<Dictionary<string, object>> selection;
            using (var dlg = new RenamePickerDialog(plan))
            {
                if (dlg.ShowDialog() != DialogResult.OK) return;
                selection = dlg.Selection;
                if (selection.Count == 0) return;
            }

            string selPath = Path.Combine(Path.GetTempPath(), "vortex_rename_selection.json");
            try
            {
                File.WriteAllText(selPath, new JavaScriptSerializer().Serialize(
                    new Dictionary<string, object> { { "selected", selection } }));
            }
            catch (Exception ex)
            {
                HubDialog.Show(HubDialog.Kind.Error, "Rename", "Couldn't stage the selection.  " + ex.Message);
                return;
            }

            int n = selection.Count;
            RunBackground(btn, "Renaming...", q + "--apply \"" + selPath + "\"", (c2, so2, se2) =>
            {
                try { File.Delete(selPath); } catch { }
                if (c2 == 0)
                {
                    HubDialog.Show(HubDialog.Kind.Success, "Renamed", "Renamed " + n + " file" + (n == 1 ? "" : "s") + ".");
                    Outcome("Rename \u00b7 renamed " + n + " file" + (n == 1 ? "" : "s"));
                }
                else
                {
                    HubDialog.Show(HubDialog.Kind.Error, "Rename failed",
                        FirstLine(se2.Length > 0 ? se2 : so2), NewestLog("rename_queue_*.log"));
                    Outcome("Rename \u00b7 rename failed");
                }
            }, "Rename \u00b7 renaming " + n + " file" + (n == 1 ? "" : "s") + " in place\u2026");
        });
    }

    // Reads generated\rename_plan.json (written by rename_queue_inplace.py
    // --plan). Returns null on unreadable/absent (caller reports).
    static RenamePlan ReadRenamePlan()
    {
        try
        {
            return new JavaScriptSerializer { MaxJsonLength = 64 * 1024 * 1024 }
                .Deserialize<RenamePlan>(File.ReadAllText(
                    Path.Combine(ProjectRoot, "generated", "rename_plan.json")));
        }
        catch { return null; }
    }

    // "File" - move download-queue videos into their real E:\Video archive
    // folders, TMDB-verified. VORTEX drives the app's file_queue.py:
    // `--plan` (background) -> pick-what-to-move dialog -> `--execute`
    // (background). Both Python runs are off the UI thread; only the modal
    // picker blocks (you're choosing). file_queue.py holds its own lock.
    // Exit 0 = clean, 2 = done with skips, 1 = error. Log: logs\file_queue_*.log.
    static void FileDownloads(Button btn)
    {
        string scriptPath = Path.Combine(ProjectRoot, "pipeline", "file_queue.py");
        if (!File.Exists(PythonExe) || !File.Exists(scriptPath))
        {
            HubDialog.Show(HubDialog.Kind.Error, "File", "Couldn't find Python or pipeline\\file_queue.py.");
            return;
        }
        string q = "\"" + scriptPath + "\" ";

        RunBackground(btn, "Planning...", q + "--plan", (code, so, se) =>
        {
            if (code == 1)
            {
                HubDialog.Show(HubDialog.Kind.Error, "File",
                    "Couldn't build the filing plan.  " + FirstLine(se.Length > 0 ? se : so));
                return;
            }

            FilePlan plan = ReadFilePlan();
            if (plan == null) return;

            // Stale plan = the app's media_db.json snapshot is older than the
            // queue, so anything downloaded since its last library refresh is
            // invisible here (the exact recurring friction: Rename scans live
            // and finds a file, File - built from the snapshot - says nothing
            // to do). the app built `file_queue.py --refresh` for this: it runs
            // their own startup quick-scan (heal_reorg_state -> scan_media ->
            // enrich_tmdb -> consolidate), rewrites media_db.json, then plans
            // against fresh data. Guarded by file_queue.py's own lock. No
            // The app window, no HTTP, no manual round trip. Do it once,
            // automatically, then carry on with whatever the fresh plan says.
            if (plan.data_stale)
            {
                RunBackground(btn, "Refreshing library...", q + "--refresh --plan", (rc, rso, rse) =>
                {
                    FilePlan fresh = ReadFilePlan();
                    if (fresh == null) return;
                    // Even if it somehow still reads stale (or --refresh hit a
                    // partial failure and planned against what it had), proceed
                    // with the freshest plan we could get rather than nagging.
                    ContinueFileFlow(btn, q, fresh);
                });
                return;
            }

            ContinueFileFlow(btn, q, plan);
        });
    }

    // Reads generated\file_queue_plan.json (written by file_queue.py --plan /
    // --refresh --plan). Returns null and shows the error if it can't be read.
    static FilePlan ReadFilePlan()
    {
        try
        {
            return new JavaScriptSerializer { MaxJsonLength = 64 * 1024 * 1024 }
                .Deserialize<FilePlan>(File.ReadAllText(
                    Path.Combine(ProjectRoot, "generated", "file_queue_plan.json")));
        }
        catch (Exception ex)
        {
            HubDialog.Show(HubDialog.Kind.Error, "File", "Couldn't read the filing plan.  " + ex.Message);
            return null;
        }
    }

    // The rest of the File flow once we have a plan we're happy with:
    // empty-check -> pick-what-to-move dialog -> --execute (background).
    static void ContinueFileFlow(Button btn, string q, FilePlan plan)
    {
            var items = plan.items ?? new FilePlanItem[0];
            var unmatched = plan.unmatched ?? new FilePlanUnmatchedItem[0];
            bool hasJunk = plan.junk_count > 0;
            if (items.Length == 0 && !hasJunk)
            {
                // Files can genuinely be sitting in the queue (real video,
                // nothing stale) and still never make it into items[] - TMDB
                // just couldn't confirm the match, so file_queue.py refuses
                // to file on a guess. That's not "nothing to do"; it needs a
                // human to fix the match in the app. Previously this branch
                // only looked at items[]/junk[] and unmatched[] was never
                // even read into C#, so 2 real unmatched queue files reported
                // as "Nothing in the download queue to file right now" -
                // confirmed live, 2026-09-30.
                if (unmatched.Length > 0)
                {
                    string umsg = unmatched.Length + " file(s) are in the download queue but TMDB "
                                + "couldn't confirm them, so they were never auto-filed:\n";
                    foreach (var u in unmatched)
                        umsg += "\n• " + (u.show_title ?? "?") + " - " + (u.reason ?? "unmatched")
                              + "  (" + Path.GetFileName(u.src ?? "") + ")";
                    umsg += "\n\nFix the match in the app (Admin panel), then try File again.";
                    HubDialog.Show(HubDialog.Kind.Warn, "File", umsg);
                    Outcome("File \u00b7 " + unmatched.Length + " file" + (unmatched.Length == 1 ? "" : "s") + " couldn't be matched to TMDB");
                    return;
                }

                string msg = "Nothing in the download queue to file right now.";
                if (plan.stale_count > 0)
                    msg += "\n\n(" + plan.stale_count + " queue entries are empty leftover folders "
                         + "- their video was already filed. A library refresh clears those.)";
                // Reaching here with data_stale still set means the automatic
                // `--refresh` up in FileDownloads ran but didn't fully clear it
                // (a quick-scan script failed partway, or the queue changed
                // again mid-scan). Rare - say what's true and point at the one
                // thing VORTEX can't do itself.
                if (plan.data_stale)
                    msg += "\n\nThe app's data still looks behind the queue even after an automatic "
                         + "refresh (last snapshot " + FormatWhen(plan.media_db_refreshed_at)
                         + "). Open the app, run Refresh library, then try File again.";
                HubDialog.Show(HubDialog.Kind.Info, "File", msg);
                Outcome("File \u00b7 nothing to file right now");
                return;
            }

            List<string> selected;
            bool sweepJunk;
            using (var dlg = new FilePickerDialog(plan))
            {
                if (dlg.ShowDialog() != DialogResult.OK) return;
                if (dlg.Selected.Count == 0 && !dlg.SweepJunk) return;
                selected = new List<string>(dlg.Selected);
                sweepJunk = dlg.SweepJunk;
            }

            string selPath = Path.Combine(Path.GetTempPath(), "vortex_file_selection.json");
            try
            {
                File.WriteAllText(selPath, new JavaScriptSerializer().Serialize(
                    new Dictionary<string, object> { { "selected", selected }, { "sweep_junk", sweepJunk } }));
            }
            catch (Exception ex)
            {
                HubDialog.Show(HubDialog.Kind.Error, "File", "Couldn't stage the selection.  " + ex.Message);
                return;
            }

            int n = selected.Count;
            RunBackground(btn, "Filing...", q + "--execute \"" + selPath + "\"", (c2, so2, se2) =>
            {
                try { File.Delete(selPath); } catch { }
                string done = GrepLine(so2, "Done:") ?? ("Filed " + n + " item" + (n == 1 ? "" : "s") + ".");
                if (c2 == 0)
                    HubDialog.Show(HubDialog.Kind.Success, "Filed", done);
                else if (c2 == 2)
                    HubDialog.Show(HubDialog.Kind.Warn, "Filed with skips",
                        done + "  Some items were skipped or flagged.", NewestLog("file_queue_*.log"));
                else
                    HubDialog.Show(HubDialog.Kind.Error, "Filing failed",
                        FirstLine(se2.Length > 0 ? se2 : so2), NewestLog("file_queue_*.log"));
                Outcome(c2 == 0 || c2 == 2 ? "File \u00b7 " + done : "File \u00b7 filing failed");
            }, "File \u00b7 moving " + n + " file" + (n == 1 ? "" : "s") + " into your library\u2026");
    }

    static Bitmap LoadScaledBitmap(string path, int size)
    {
        // new Bitmap(path) - the GDI+ image decoder. For an .ico it reliably
        // returns the largest stored frame (verified against these assets)
        // and decodes PNG-compressed frames correctly, unlike System.Drawing
        // .Icon; for a .png/.jpg it's the plain decode.
        using (var src = new Bitmap(path))
        {
            var bmp = new Bitmap(size, size);
            using (var g = Graphics.FromImage(bmp))
            {
                g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                g.CompositingQuality = System.Drawing.Drawing2D.CompositingQuality.HighQuality;
                g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
                g.Clear(Color.Transparent);
                float scale = Math.Min((float)size / src.Width, (float)size / src.Height);
                int w = (int)(src.Width * scale);
                int h = (int)(src.Height * scale);
                g.DrawImage(src, (size - w) / 2, (size - h) / 2, w, h);
            }
            return bmp;
        }
    }

    static void SafeInvoke(Action action)
    {
        try { action(); }
        catch (Exception ex) { HubDialog.Show(HubDialog.Kind.Error, "Something went wrong", ex.Message); }
    }

    // Small text helpers for turning a script's raw stdout/stderr into one
    // clean line for HubDialog instead of dumping the whole thing.
    internal static string FirstLine(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        foreach (var raw in s.Replace("\r", "").Split('\n'))
        {
            string line = raw.Trim();
            if (line.Length == 0) continue;
            return line.Length > 260 ? line.Substring(0, 260) + "…" : line;
        }
        return "";
    }

    internal static string GrepLine(string s, string startsWith)
    {
        if (string.IsNullOrEmpty(s)) return null;
        foreach (var raw in s.Replace("\r", "").Split('\n'))
            if (raw.TrimStart().StartsWith(startsWith)) return raw.Trim();
        return null;
    }

    internal static string NewestLog(string pattern)
    {
        try
        {
            string best = null; DateTime bt = DateTime.MinValue;
            foreach (var f in Directory.GetFiles(Path.Combine(ProjectRoot, "logs"), pattern))
            {
                var t = File.GetLastWriteTime(f);
                if (t > bt) { bt = t; best = f; }
            }
            return best;
        }
        catch { return null; }
    }

    // ---- code-drawn line icons -----------------------------------------
    // One visual language for every button, each tinted to its own accent
    // colour. Drawn as vectors so they stay crisp at any size and never
    // need asset files.

    internal enum GlyphKind { None, FolderDown, Trash, Shield, Pencil, FolderIn, Recycle, Document, Detective, Lighthouse }

    internal static Bitmap GlyphBitmap(GlyphKind kind, int size, Color color)
    {
        var bmp = new Bitmap(size, size);
        using (var g = Graphics.FromImage(bmp))
        {
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            g.Clear(Color.Transparent);
            float pad = size * 0.13f;
            var b = new RectangleF(pad, pad, size - pad * 2, size - pad * 2);
            float sw = Math.Max(1.5f, size / 12.5f);
            using (var p = new Pen(color, sw))
            {
                p.StartCap = p.EndCap = System.Drawing.Drawing2D.LineCap.Round;
                p.LineJoin = System.Drawing.Drawing2D.LineJoin.Round;
                DrawGlyph(g, kind, b, p);
            }
        }
        return bmp;
    }

    static void GArrow(Graphics g, Pen p, float x1, float y1, float x2, float y2, float head)
    {
        g.DrawLine(p, x1, y1, x2, y2);
        double a = Math.Atan2(y2 - y1, x2 - x1);
        g.DrawLine(p, x2, y2, x2 - head * (float)Math.Cos(a - 0.5), y2 - head * (float)Math.Sin(a - 0.5));
        g.DrawLine(p, x2, y2, x2 - head * (float)Math.Cos(a + 0.5), y2 - head * (float)Math.Sin(a + 0.5));
    }

    static void DrawGlyph(Graphics g, GlyphKind kind, RectangleF b, Pen p)
    {
        float x = b.X, y = b.Y, w = b.Width, h = b.Height, cx = x + w / 2f, cy = y + h / 2f;
        switch (kind)
        {
            case GlyphKind.FolderDown:
            case GlyphKind.FolderIn:
            {
                float bodyTop = y + h * 0.24f;
                using (var body = RoundedRectPath(new RectangleF(x, bodyTop, w, h * 0.58f), h * 0.10f))
                    g.DrawPath(p, body);
                g.DrawLines(p, new[] {
                    new PointF(x + w * 0.04f, bodyTop),
                    new PointF(x + w * 0.10f, y + h * 0.06f),
                    new PointF(x + w * 0.46f, y + h * 0.06f),
                    new PointF(x + w * 0.54f, bodyTop),
                });
                if (kind == GlyphKind.FolderDown)
                    GArrow(g, p, cx, bodyTop + h * 0.06f, cx, y + h * 0.72f, w * 0.16f);
                else
                    GArrow(g, p, x + w * 0.14f, cy + h * 0.06f, cx + w * 0.08f, cy + h * 0.06f, w * 0.16f);
                break;
            }
            case GlyphKind.Trash:
            {
                g.DrawLine(p, x + w * 0.06f, y + h * 0.20f, x + w * 0.94f, y + h * 0.20f);
                g.DrawLines(p, new[] {
                    new PointF(x + w * 0.36f, y + h * 0.20f),
                    new PointF(x + w * 0.40f, y + h * 0.07f),
                    new PointF(x + w * 0.60f, y + h * 0.07f),
                    new PointF(x + w * 0.64f, y + h * 0.20f),
                });
                g.DrawLines(p, new[] {
                    new PointF(x + w * 0.16f, y + h * 0.20f),
                    new PointF(x + w * 0.22f, y + h * 0.93f),
                    new PointF(x + w * 0.78f, y + h * 0.93f),
                    new PointF(x + w * 0.84f, y + h * 0.20f),
                });
                for (int i = 0; i < 3; i++)
                {
                    float lx = x + w * (0.34f + i * 0.16f);
                    g.DrawLine(p, lx, y + h * 0.32f, lx, y + h * 0.82f);
                }
                break;
            }
            case GlyphKind.Shield:
            {
                using (var s = new System.Drawing.Drawing2D.GraphicsPath())
                {
                    s.AddLines(new[] {
                        new PointF(cx, y + h * 0.04f),
                        new PointF(x + w * 0.92f, y + h * 0.20f),
                        new PointF(x + w * 0.92f, y + h * 0.52f),
                    });
                    s.AddBezier(
                        new PointF(x + w * 0.92f, y + h * 0.52f),
                        new PointF(x + w * 0.86f, y + h * 0.82f),
                        new PointF(cx + w * 0.14f, y + h * 0.92f),
                        new PointF(cx, y + h * 0.98f));
                    s.AddBezier(
                        new PointF(cx, y + h * 0.98f),
                        new PointF(cx - w * 0.14f, y + h * 0.92f),
                        new PointF(x + w * 0.14f, y + h * 0.82f),
                        new PointF(x + w * 0.08f, y + h * 0.52f));
                    s.AddLines(new[] {
                        new PointF(x + w * 0.08f, y + h * 0.52f),
                        new PointF(x + w * 0.08f, y + h * 0.20f),
                        new PointF(cx, y + h * 0.04f),
                    });
                    g.DrawPath(p, s);
                }
                g.DrawLines(p, new[] {
                    new PointF(x + w * 0.34f, cy + h * 0.02f),
                    new PointF(x + w * 0.46f, cy + h * 0.16f),
                    new PointF(x + w * 0.68f, cy - h * 0.16f),
                });
                break;
            }
            case GlyphKind.Pencil:
            {
                g.DrawLines(p, new[] {
                    new PointF(x + w * 0.10f, y + h * 0.90f),
                    new PointF(x + w * 0.68f, y + h * 0.14f),
                    new PointF(x + w * 0.90f, y + h * 0.32f),
                    new PointF(x + w * 0.30f, y + h * 0.96f),
                    new PointF(x + w * 0.10f, y + h * 0.90f),
                });
                g.DrawLine(p, x + w * 0.10f, y + h * 0.90f, x + w * 0.20f, y + h * 0.66f);
                break;
            }
            case GlyphKind.Recycle:
            {
                for (int i = 0; i < 3; i++)
                {
                    float a0 = i * 120f - 60f;
                    g.DrawArc(p, x + w * 0.12f, y + h * 0.12f, w * 0.76f, h * 0.76f, a0, 74f);
                    double rad = (a0 + 74f) * Math.PI / 180.0;
                    float ex = cx + (float)Math.Cos(rad) * w * 0.38f;
                    float ey = cy + (float)Math.Sin(rad) * h * 0.38f;
                    double t = rad + Math.PI / 2;
                    GArrow(g, p, ex - (float)Math.Cos(t) * w * 0.001f, ey - (float)Math.Sin(t) * h * 0.001f,
                           ex, ey, w * 0.14f);
                }
                break;
            }
            case GlyphKind.Document:
            {
                using (var pg = new System.Drawing.Drawing2D.GraphicsPath())
                {
                    pg.AddLines(new[] {
                        new PointF(x + w * 0.16f, y + h * 0.04f),
                        new PointF(x + w * 0.64f, y + h * 0.04f),
                        new PointF(x + w * 0.84f, y + h * 0.24f),
                        new PointF(x + w * 0.84f, y + h * 0.96f),
                        new PointF(x + w * 0.16f, y + h * 0.96f),
                        new PointF(x + w * 0.16f, y + h * 0.04f),
                    });
                    g.DrawPath(p, pg);
                }
                g.DrawLines(p, new[] {
                    new PointF(x + w * 0.64f, y + h * 0.04f),
                    new PointF(x + w * 0.64f, y + h * 0.24f),
                    new PointF(x + w * 0.84f, y + h * 0.24f),
                });
                for (int i = 0; i < 3; i++)
                {
                    float ly = y + h * (0.42f + i * 0.18f);
                    g.DrawLine(p, x + w * 0.28f, ly, x + w * 0.72f, ly);
                }
                break;
            }
            case GlyphKind.Detective:
            {
                // A fedora, not another lens - "detective" read at a glance.
                // Domed crown + two sides down to the brim + a band, then
                // the brim's own under-curve; a small centre pinch on the
                // crown for character.
                var crown = new RectangleF(x + w * 0.20f, y + h * 0.12f, w * 0.60f, h * 0.46f);
                g.DrawArc(p, crown, 180f, 180f);
                g.DrawLine(p, crown.Left, crown.Top + crown.Height * 0.5f, x + w * 0.06f, y + h * 0.62f);
                g.DrawLine(p, crown.Right, crown.Top + crown.Height * 0.5f, x + w * 0.94f, y + h * 0.62f);
                g.DrawLine(p, crown.Left, crown.Top + crown.Height * 0.78f, crown.Right, crown.Top + crown.Height * 0.78f);
                var brim = new RectangleF(x + w * 0.02f, y + h * 0.55f, w * 0.96f, h * 0.16f);
                g.DrawArc(p, brim, 0f, 180f);
                g.DrawLine(p, x + w * 0.5f, crown.Top + 1f, x + w * 0.5f, crown.Top + crown.Height * 0.30f);
                break;
            }
            case GlyphKind.Lighthouse:
            {
                // Tapered banded tower, gallery ledge, lantern room + roof,
                // a lit lamp with short rays. Same line language as the
                // rest; one solid dot for the light itself.
                float ly = y + h * 0.31f;
                using (var ray = new Pen(p.Color, Math.Max(1f, p.Width * 0.7f)))
                {
                    ray.StartCap = ray.EndCap = System.Drawing.Drawing2D.LineCap.Round;
                    g.DrawLine(ray, cx - w * 0.20f, ly, x - w * 0.02f, y + h * 0.15f);
                    g.DrawLine(ray, cx - w * 0.20f, ly, x - w * 0.04f, y + h * 0.42f);
                    g.DrawLine(ray, cx + w * 0.20f, ly, x + w * 1.02f, y + h * 0.15f);
                    g.DrawLine(ray, cx + w * 0.20f, ly, x + w * 1.04f, y + h * 0.42f);
                }
                float baseY = y + h * 0.92f, deckY = y + h * 0.40f;
                g.DrawLine(p, x + w * 0.08f, baseY, x + w * 0.92f, baseY);   // rock line
                using (var tower = new System.Drawing.Drawing2D.GraphicsPath())
                {
                    tower.AddPolygon(new[] {
                        new PointF(cx - w * 0.30f, baseY),
                        new PointF(cx - w * 0.19f, deckY),
                        new PointF(cx + w * 0.19f, deckY),
                        new PointF(cx + w * 0.30f, baseY),
                    });
                    g.DrawPath(p, tower);
                }
                g.DrawLine(p, cx - w * 0.245f, y + h * 0.58f, cx + w * 0.245f, y + h * 0.58f);  // band
                g.DrawLine(p, cx - w * 0.215f, y + h * 0.75f, cx + w * 0.215f, y + h * 0.75f);  // band
                g.DrawLine(p, cx - w * 0.26f, deckY, cx + w * 0.26f, deckY);                    // gallery ledge
                g.DrawRectangle(p, cx - w * 0.17f, y + h * 0.22f, w * 0.34f, h * 0.18f);        // lantern room
                g.DrawLine(p, cx, y + h * 0.22f, cx, deckY);                                    // mullion
                using (var roof = new System.Drawing.Drawing2D.GraphicsPath())
                {
                    roof.AddPolygon(new[] {
                        new PointF(cx, y + h * 0.05f),
                        new PointF(cx - w * 0.22f, y + h * 0.22f),
                        new PointF(cx + w * 0.22f, y + h * 0.22f),
                    });
                    g.DrawPath(p, roof);
                }
                using (var lamp = new SolidBrush(p.Color))
                    g.FillEllipse(lamp, cx - w * 0.09f, ly - h * 0.09f, w * 0.18f, h * 0.18f);
                break;
            }
        }
    }

    // ---- aware-launcher status -------------------------------------------
    // Everything here is best-effort and read-only: a missing file, an
    // unreachable port, or a zero count just means "show nothing". Refreshed
    // on a background thread at open and (throttled) on window activation,
    // so a slow TCP probe never stalls the UI.

    static volatile HubStatus _status = new HubStatus();
    static DateTime _lastStatusUtc = DateTime.MinValue;
    internal static HubStatus Status { get { return _status; } }

    internal static string AppDisplayName { get { return AppName; } }

    static string SystemStatusPath() { return Path.Combine(ProjectRoot, "data", "system_status.json"); }
    static string PendingDeletionPath() { return Path.Combine(ProjectRoot, "Pending Deletion"); }

    // `target` is the StatusStrip alone, not the form - status only ever
    // changes what that one control draws, so that's the only thing that
    // should repaint. Invalidating the whole form here (as this used to)
    // forced every owner-drawn button and the portal ring to fully redraw
    // a beat after the window had already finished painting itself - the
    // visible "flash/blink" on open the user reported.
    static void RefreshStatusAsync(Control target)
    {
        _lastStatusUtc = DateTime.UtcNow;
        var th = new System.Threading.Thread(() =>
        {
            var s = new HubStatus();
            s.AppUp = PortOpen(AppPort);
            try
            {
                string p = SystemStatusPath();
                if (File.Exists(p))
                {
                    string j = File.ReadAllText(p);
                    s.MissingTotal = JsonInt(j, "missing_total");
                    // written_at = when the file was generated (the app added
                    // it 2026-08-28 at VORTEX's request); prefer it for the
                    // staleness check, fall back to last_refresh if absent.
                    s.AppStale = IsStale(JsonString(j, "written_at") ?? JsonString(j, "last_refresh"));
                }
            }
            catch { }
            try
            {
                string pd = PendingDeletionPath();
                if (Directory.Exists(pd))
                    s.PendingCount = Directory.GetFileSystemEntries(pd)
                        .Count(e => !Path.GetFileName(e).Equals("desktop.ini", StringComparison.OrdinalIgnoreCase));
            }
            catch { }

            _status = s;
            try { if (!target.IsDisposed) target.BeginInvoke((Action)(() => { if (!target.IsDisposed) target.Invalidate(); })); }
            catch { }
        });
        th.IsBackground = true;
        th.Start();
    }

    static bool PortOpen(int port)
    {
        try
        {
            using (var c = new TcpClient())
            {
                var ar = c.BeginConnect("127.0.0.1", port, null, null);
                bool ok = ar.AsyncWaitHandle.WaitOne(250) && c.Connected;
                if (ok) { try { c.EndConnect(ar); } catch { } }
                return ok;
            }
        }
        catch { return false; }
    }

    static long? JsonInt(string json, string key)
    {
        var m = System.Text.RegularExpressions.Regex.Match(
            json, "\"" + System.Text.RegularExpressions.Regex.Escape(key) + "\"\\s*:\\s*(-?\\d+)");
        long v;
        return m.Success && long.TryParse(m.Groups[1].Value, out v) ? (long?)v : null;
    }

    static string JsonString(string json, string key)
    {
        var m = System.Text.RegularExpressions.Regex.Match(
            json, "\"" + System.Text.RegularExpressions.Regex.Escape(key) + "\"\\s*:\\s*\"([^\"]*)\"");
        return m.Success ? m.Groups[1].Value : null;
    }

    // Human-readable local time for one of file_queue.py's UTC ISO
    // timestamps (media_db_refreshed_at / newest_queue_mtime). Falls back
    // to the raw string if it doesn't parse, rather than hiding it.
    internal static string FormatWhen(string iso)
    {
        if (string.IsNullOrEmpty(iso)) return "unknown";
        DateTime dt;
        if (DateTime.TryParse(iso, System.Globalization.CultureInfo.InvariantCulture,
            System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal, out dt))
            return dt.ToLocalTime().ToString("MMM d, h:mm tt");
        return iso;
    }

    static bool IsStale(string iso)
    {
        if (string.IsNullOrEmpty(iso)) return false;
        DateTime dt;
        if (DateTime.TryParse(iso, System.Globalization.CultureInfo.InvariantCulture,
            System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal, out dt))
            return DateTime.UtcNow - dt > TimeSpan.FromHours(24);
        return false;
    }

    [DllImport("user32.dll")]
    static extern bool ReleaseCapture();

    [DllImport("user32.dll")]
    static extern IntPtr SendMessage(IntPtr hWnd, int msg, IntPtr wParam, IntPtr lParam);

    // Borderless-window drag: on left-press over an empty area, hand the
    // drag straight to Windows (WM_NCLBUTTONDOWN / HTCAPTION) so it behaves
    // exactly like a real title bar - Aero snap included.
    internal static void MakeDraggable(Control c, Form form)
    {
        c.MouseDown += (s, e) =>
        {
            if (e.Button != MouseButtons.Left) return;
            ReleaseCapture();
            SendMessage(form.Handle, 0xA1, (IntPtr)2, IntPtr.Zero);
        };
    }

    // Sends every top-level item in Pending Deletion to the Recycle Bin -
    // RecycleOption.SendToRecycleBin (Microsoft.VisualBasic.FileIO), never
    // a permanent delete, so anything sent here is still recoverable from
    // the Recycle Bin afterward if it turns out to be a mistake. Leaves
    // desktop.ini alone (it's what gives the folder its trash-can icon,
    // not something eligible for review/deletion in the first place) and
    // leaves the Pending Deletion folder itself in place, ready for the
    // next purge.
    static void EmptyPendingDeletion(Button btn, string dir)
    {
        if (!Directory.Exists(dir))
        {
            HubDialog.Show(HubDialog.Kind.Warn, "Empty to Recycle Bin", "The Pending Deletion folder doesn't exist:\n" + dir);
            return;
        }
        var entries = Directory.GetFileSystemEntries(dir)
            .Where(e => !Path.GetFileName(e).Equals("desktop.ini", StringComparison.OrdinalIgnoreCase))
            .ToArray();
        if (entries.Length == 0)
        {
            HubDialog.Show(HubDialog.Kind.Info, "Empty to Recycle Bin", "Nothing in Pending Deletion right now.");
            return;
        }
        if (!HubDialog.Confirm("Empty to Recycle Bin",
                "Send " + entries.Length + " item" + (entries.Length == 1 ? "" : "s")
                + " in Pending Deletion to the Recycle Bin?\n\nNot permanent - you can restore from the Recycle Bin afterward.",
                "Send to Recycle Bin"))
            return;

        // Off the UI thread - a big Pending Deletion can take a while, and
        // the launch pad must stay usable meanwhile.
        string original = btn.Text;
        var hub = btn as HubButton;
        if (hub != null) hub.Busy = true; else btn.Text = "Emptying...";
        btn.Enabled = false;
        Say("Empty to Recycle Bin \u00b7 sending " + entries.Length + " item" + (entries.Length == 1 ? "" : "s") + " to the Recycle Bin\u2026");
        var th = new System.Threading.Thread(() =>
        {
            int moved = 0;
            var failures = new List<string>();
            foreach (var entry in entries)
            {
                try
                {
                    if (Directory.Exists(entry))
                        FileSystem.DeleteDirectory(entry, UIOption.OnlyErrorDialogs, RecycleOption.SendToRecycleBin);
                    else
                        FileSystem.DeleteFile(entry, UIOption.OnlyErrorDialogs, RecycleOption.SendToRecycleBin);
                    moved++;
                }
                catch (Exception ex) { failures.Add(Path.GetFileName(entry) + ": " + ex.Message); }
            }

            int fm = moved; var ff = failures;
            try
            {
                if (!btn.IsDisposed)
                    btn.BeginInvoke((Action)(() =>
                    {
                        if (hub != null) hub.Busy = false; else btn.Text = original;
                        btn.Enabled = true;
                        if (Caption != null) Caption.Clear();
                        string summary = fm + " item" + (fm == 1 ? "" : "s") + " sent to the Recycle Bin.";
                        if (ff.Count > 0)
                            HubDialog.Show(HubDialog.Kind.Warn, "Empty to Recycle Bin",
                                summary + "\n" + ff.Count + " couldn't be moved:\n" + string.Join("\n", ff.Take(4)));
                        else
                            HubDialog.Show(HubDialog.Kind.Success, "Empty to Recycle Bin", summary);
                        Outcome("Empty to Recycle Bin \u00b7 " + summary);
                    }));
            }
            catch { }
        });
        th.IsBackground = true;
        th.Start();
    }

    static void OpenFolder(string path)
    {
        if (!Directory.Exists(path))
        {
            HubDialog.Show(HubDialog.Kind.Warn, "Open folder", "That folder doesn't exist:\n" + path);
            return;
        }
        Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
    }

    static void LaunchProcess(string fileName, string arguments, string workingDirectory)
    {
        var psi = new ProcessStartInfo
        {
            FileName = fileName,
            Arguments = arguments,
            WorkingDirectory = workingDirectory,
            UseShellExecute = false,
        };
        Process.Start(psi);
    }

    // Start the app if its port isn't serving yet (python run.py, no console
    // window), then open it in the default browser. No UI-thread wait after
    // starting: the browser's own cold start covers the moment the server
    // needs to bind, and the page retries until it answers.
    static void LaunchApp()
    {
        if (!TcpPortOpen(AppPort))
        {
            string runPy = Path.Combine(ProjectRoot, "run.py");
            if (PythonExe == null || !File.Exists(runPy))
            {
                HubDialog.Show(HubDialog.Kind.Error, AppName,
                    "Couldn't find Python or run.py.\nSet \"python_exe\" in vortex.json, or start the app by hand: python run.py");
                return;
            }
            Process.Start(new ProcessStartInfo
            {
                FileName = PythonExe,
                Arguments = "\"" + runPy + "\" --no-browser",
                WorkingDirectory = ProjectRoot,
                UseShellExecute = false,
                CreateNoWindow = true,
            });
        }
        try { Process.Start(new ProcessStartInfo("http://127.0.0.1:" + AppPort) { UseShellExecute = true }); } catch { }
    }

    static bool TcpPortOpen(int port)
    {
        try
        {
            using (var c = new System.Net.Sockets.TcpClient())
            {
                var ar = c.BeginConnect("127.0.0.1", port, null, null);
                if (!ar.AsyncWaitHandle.WaitOne(250)) return false;
                c.EndConnect(ar);
                return true;
            }
        }
        catch { return false; }
    }
}

// Double-buffered form - the owner-drawn HubButtons repaint on hover/press
// and (later) an animation timer; without this their transparent corners
// flicker against the background image. Also lays a vignette scrim over
// the Time Vortex image so the foreground panels read against calm ground.
class HubForm : Form
{
    public HubForm() { DoubleBuffered = true; }

    // CS_DROPSHADOW - a soft shadow around the borderless window so it
    // reads as a floating app rather than bleeding into the desktop.
    protected override CreateParams CreateParams
    {
        get { var cp = base.CreateParams; cp.ClassStyle |= 0x00020000; return cp; }
    }

    // Cached composite of the STATIC background layers (stretched image +
    // calming wash + radial vignette). Rebuilt only on a size change; every
    // frame the animated FX layers are drawn fresh over a blit of this, so
    // the 60fps background repaint stays "one blit + a dozen arcs".
    Bitmap _bg;

    void EnsureBg()
    {
        var sz = ClientSize;
        if (sz.Width <= 0 || sz.Height <= 0) return;
        if (_bg != null && _bg.Size == sz) return;
        if (_bg != null) _bg.Dispose();
        _bg = new Bitmap(sz.Width, sz.Height);
        using (var g = Graphics.FromImage(_bg))
        {
            g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
            var r = new Rectangle(0, 0, sz.Width, sz.Height);
            if (BackgroundImage != null) g.DrawImage(BackgroundImage, r);
            else using (var b = new SolidBrush(Color.FromArgb(6, 8, 16))) g.FillRectangle(b, r);

            using (var wash = new SolidBrush(Color.FromArgb(48, 4, 6, 14)))
                g.FillRectangle(wash, r);

            using (var path = new System.Drawing.Drawing2D.GraphicsPath())
            {
                var big = new Rectangle(r.X - r.Width / 4, r.Y - r.Height / 4,
                                        r.Width * 3 / 2, r.Height * 3 / 2);
                path.AddEllipse(big);
                using (var pgb = new System.Drawing.Drawing2D.PathGradientBrush(path))
                {
                    pgb.CenterPoint = new PointF(r.X + r.Width / 2f, r.Y + r.Height * 0.42f);
                    pgb.CenterColor = Color.FromArgb(0, 0, 0, 0);
                    pgb.SurroundColors = new[] { Color.FromArgb(150, 2, 3, 10) };
                    pgb.FocusScales = new PointF(0.35f, 0.30f);
                    g.FillPath(pgb, path);
                }
            }
        }
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        using (var pen = new Pen(Color.FromArgb(90, 120, 150, 200)))
            e.Graphics.DrawRectangle(pen, 0, 0, ClientSize.Width - 1, ClientSize.Height - 1);
    }

    protected override void OnPaintBackground(PaintEventArgs e)
    {
        EnsureBg();
        var g = e.Graphics;
        var r = ClientRectangle;
        if (r.Width <= 0 || r.Height <= 0 || _bg == null) { base.OnPaintBackground(e); return; }
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        g.DrawImageUnscaled(_bg, 0, 0);

        // Everything that moves lives inside the portal control (which
        // repaints itself each frame with no window repaint = no header
        // flicker). The scene tint below is a FIXED value on purpose: it used
        // to drift warm at night by reading the clock on every paint, and
        // since the portal/status strip repaint constantly while the rest of
        // the form doesn't, a window left open across the change showed a
        // brown form with a correct-coloured square around the portal.
        if (Vortex.Fx.SceneTint) DrawSceneTint(g, r);
    }

    // The standard (formerly "midday") cool tint, at all times of day.
    static void DrawSceneTint(Graphics g, Rectangle r)
    {
        using (var b = new SolidBrush(Color.FromArgb(12, 55, 125, 215)))
            g.FillRectangle(b, r);
    }
}

// Owner-drawn button: an anti-aliased rounded panel over the background
// image, with hover + press feedback and one of three content layouts.
// Replaces the flat square WinForms buttons - those can't round their
// corners against a background image without a hard-edged Region clip.
// Colours/really-shared helpers come from Vortex (same assembly).
class HubButton : Button
{
    public enum ContentLayout { TextOnly, IconAbove, IconLeft }

    public Color Accent = Color.White;
    public int Radius = 12;
    // Translucent panel body: the Time Vortex image bleeds faintly through
    // (not just at the corners). Kept high enough that text over a very
    // busy background stays readable - the form's vignette scrim does the
    // rest of that work.
    public int FillAlpha = 210;
    public ContentLayout Mode = ContentLayout.TextOnly;
    // Optional post-paint hook (Stage 4 count pills draw here). Given the
    // rounded content rect. We can't use the normal `.Paint` event because
    // Button.OnPaint's base implementation does the stock button rendering
    // and would erase everything this class draws.
    public Action<Graphics, Rectangle> Overlay;

    // Intro settle, driven by the form's shared timer via Step().
    // 1 = dipped/hidden, 0 = home. Only moves when Fx.IntroSettle is on;
    // otherwise it stays 0 and the buttons simply appear.
    public float Anim;

    // Working state: instead of stuffing "Refreshing library..." into a
    // 140px button, the content area shows a small page-into-folder loop.
    // Set by RunBackground; the shared timer advances _busyClock.
    bool _busy;
    float _busyClock;
    public bool Busy
    {
        get { return _busy; }
        set { if (value && !_busy) _busyClock = 0f; _busy = value; Invalidate(); }
    }

    bool _hover, _pressDown;
    float _pressAmt;   // eased 0..1 press depth
    float _hoverAmt;   // eased 0..1 hover level, drives the neon glow

    public HubButton()
    {
        SetStyle(ControlStyles.SupportsTransparentBackColor
               | ControlStyles.UserPaint
               | ControlStyles.AllPaintingInWmPaint
               | ControlStyles.OptimizedDoubleBuffer
               | ControlStyles.ResizeRedraw, true);
        BackColor = Color.Transparent;
        FlatStyle = FlatStyle.Flat;
        FlatAppearance.BorderSize = 0;
        Cursor = Cursors.Hand;
        ForeColor = Color.White;
    }

    protected override bool ShowFocusCues { get { return false; } }
    protected override void OnMouseEnter(EventArgs e) { _hover = true; Invalidate(); base.OnMouseEnter(e); }
    protected override void OnMouseLeave(EventArgs e) { _hover = false; _pressDown = false; Invalidate(); base.OnMouseLeave(e); }
    protected override void OnMouseDown(MouseEventArgs e) { _pressDown = true; Invalidate(); base.OnMouseDown(e); }
    protected override void OnMouseUp(MouseEventArgs e) { _pressDown = false; Invalidate(); base.OnMouseUp(e); }
    protected override void OnTextChanged(EventArgs e) { Invalidate(); base.OnTextChanged(e); }
    protected override void OnEnabledChanged(EventArgs e) { Invalidate(); base.OnEnabledChanged(e); }

    // Called each frame by the form's shared animation timer.
    public void Step(float elapsed, float dt)
    {
        // Intro: nothing by default (buttons just appear). If Fx.IntroSettle
        // is on, every button does ONE tiny simultaneous settle - no
        // per-button stagger (that read as glitchy).
        float target = 0f;
        if (Vortex.Fx.IntroSettle && elapsed < 0.20f) target = 1f - elapsed / 0.20f;
        target = target * target * (3f - 2f * target);   // smoothstep

        float np = _pressAmt + ((_pressDown ? 1f : 0f) - _pressAmt) * Math.Min(1f, dt * 16f);
        float nh = _hoverAmt + ((_hover ? 1f : 0f) - _hoverAmt) * Math.Min(1f, dt * 12f);

        if (Math.Abs(target - Anim) > 0.0006f
            || Math.Abs(np - _pressAmt) > 0.002f || (np == 0f && _pressAmt != 0f)
            || Math.Abs(nh - _hoverAmt) > 0.002f || (nh == 0f && _hoverAmt != 0f))
        {
            Anim = target;
            _pressAmt = np;
            _hoverAmt = nh;
            Invalidate();
        }

        if (_busy) { _busyClock += dt; Invalidate(); }
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;

        // Show the Time Vortex image through the rounded corners.
        InvokePaintBackground(this, e);

        // Intro rise + press dip - translate everything drawn below.
        float rise = Anim * 12f;
        float dip = _pressAmt * 2.5f;
        if (rise != 0f || dip != 0f) g.TranslateTransform(0f, rise + dip);

        float inset = 2f + _pressAmt * 2f;
        var rect = new RectangleF(inset, inset, Width - inset * 2, Height - inset * 2);

        bool on = Enabled || _busy;   // "working" reads lit, not greyed-out
        int baseA = on ? FillAlpha : (int)(FillAlpha * 0.45f);
        int fillA = Math.Min(255, baseA + (int)(38 * (on ? _hoverAmt : 0f)) + (int)(_pressAmt * 12));
        Color line = !on ? Color.FromArgb(90, Accent)
                   : _hoverAmt > 0.02f ? ControlPaint.Light(Accent, 0.15f + 0.4f * _hoverAmt) : Accent;
        float lineW = 1.3f + (on ? _hoverAmt : 0f) * 1.1f;

        using (var path = Vortex.RoundedRectPath(rect, Radius))
        {
            using (var fill = new SolidBrush(Color.FromArgb(fillA, Vortex.PanelColor)))
                g.FillPath(fill, path);
            // Subtle glassy sheen along the top edge.
            using (var sheen = new Pen(Color.FromArgb(on ? 30 : 12, 255, 255, 255), 1f))
                g.DrawLine(sheen, rect.Left + Radius, rect.Top + 1.5f, rect.Right - Radius, rect.Top + 1.5f);
            // Neon glow on hover - an outer bleed (kept inside the control's
            // ~2px margin so it can't clip into a box) plus a few inset
            // rings of accent light pooling just inside the edge. Eases
            // in/out with _hoverAmt.
            if (_hoverAmt > 0.01f && on)
            {
                float ha = _hoverAmt;
                var rj = System.Drawing.Drawing2D.LineJoin.Round;
                using (var o1 = new Pen(Color.FromArgb((int)(64 * ha), Accent), 5f) { LineJoin = rj })
                using (var o2 = new Pen(Color.FromArgb((int)(120 * ha), Accent), 2.4f) { LineJoin = rj })
                {
                    g.DrawPath(o1, path);
                    g.DrawPath(o2, path);
                }
                for (int k = 1; k <= 3; k++)
                {
                    var ir = new RectangleF(rect.X + k * 3f, rect.Y + k * 3f,
                                            rect.Width - k * 6f, rect.Height - k * 6f);
                    if (ir.Width <= 2f || ir.Height <= 2f) break;
                    using (var ip = new Pen(Color.FromArgb((int)((44 - k * 11) * ha), Accent), 2f) { LineJoin = rj })
                    using (var ipath = Vortex.RoundedRectPath(ir, Math.Max(1f, Radius - k * 3f)))
                        g.DrawPath(ip, ipath);
                }
            }
            using (var pen = new Pen(line, lineW))
                g.DrawPath(pen, path);
        }

        var content = Rectangle.Round(rect);

        if (_busy)
        {
            DrawBusy(g, content);
            if (Overlay != null) Overlay(g, content);
            return;
        }

        Color fg = !on ? Color.FromArgb(130, ForeColor)
                 : _hoverAmt > 0.5f ? ControlPaint.Light(ForeColor, 0.25f) : ForeColor;
        var img = Enabled || Image == null ? Image : Fade(Image);

        if (Mode == ContentLayout.TextOnly || img == null)
        {
            TextRenderer.DrawText(g, Text, Font, content, fg,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter
                | TextFormatFlags.SingleLine | TextFormatFlags.EndEllipsis);
        }
        else if (Mode == ContentLayout.IconAbove)
        {
            int ix = content.Left + (content.Width - img.Width) / 2;
            int iy = content.Top + 16;
            g.DrawImage(img, ix, iy, img.Width, img.Height);
            var tr = Rectangle.FromLTRB(content.Left + 8, iy + img.Height + 8,
                                        content.Right - 8, content.Bottom - 10);
            TextRenderer.DrawText(g, Text, Font, tr, fg,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.Top | TextFormatFlags.WordBreak);
        }
        else // IconLeft - icon + label kept together as one centred unit
        {
            var ts = TextRenderer.MeasureText(g, Text, Font, Size.Empty, TextFormatFlags.NoPadding);
            const int gap = 12;
            int total = img.Width + gap + ts.Width;
            int startX = content.Left + (content.Width - total) / 2;
            int iy = content.Top + (content.Height - img.Height) / 2;
            g.DrawImage(img, startX, iy, img.Width, img.Height);
            var tr = new Rectangle(startX + img.Width + gap, content.Top, ts.Width + 4, content.Height);
            TextRenderer.DrawText(g, Text, Font, tr, fg,
                TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.SingleLine | TextFormatFlags.NoPadding);
        }

        if (Overlay != null) Overlay(g, content);
        // Deliberately NOT calling base.OnPaint(e) - Button.OnPaint runs the
        // stock button rendering, which erases the panel/border drawn above.
    }

    // "Working" indicator: a small page slides right and drops into a
    // folder, on a ~1.1s loop. Sized to sit inside even the narrow action
    // buttons. Accent-tinted, line-art like the button glyphs.
    void DrawBusy(Graphics g, Rectangle c)
    {
        float mid = c.Top + c.Height / 2f;
        const float groupW = 84f;
        float gx = c.Left + (c.Width - groupW) / 2f;
        float fx = gx + groupW - 19f;          // folder left edge
        var round = System.Drawing.Drawing2D.LineJoin.Round;

        // folder (static)
        using (var fp = new Pen(Accent, 1.7f) { LineJoin = round })
        {
            const float fw = 19f, fh = 13f;
            float ft = mid - fh / 2f + 2f;
            g.DrawLines(fp, new[]
            {
                new PointF(fx, ft + fh), new PointF(fx, ft),
                new PointF(fx + 4f, ft), new PointF(fx + 6f, ft - 3f),
                new PointF(fx + 11f, ft - 3f), new PointF(fx + 13f, ft),
                new PointF(fx + fw, ft), new PointF(fx + fw, ft + fh),
                new PointF(fx, ft + fh),
            });
        }

        // page: travels gx+3 -> fx over the first ~82% of the loop, then
        // shrinks + fades as it "drops in".
        float p = (_busyClock * 0.9f) % 1f;
        float travel = Math.Min(1f, p / 0.82f);
        float px = gx + 3f + travel * (fx - (gx + 3f));
        float fade = p < 0.72f ? 1f : Math.Max(0f, 1f - (p - 0.72f) / 0.28f);
        float sc = 0.6f + 0.4f * fade;
        int a = (int)(235 * fade);
        if (a > 4)
        {
            float pw = 11f * sc, ph = 14f * sc, pt = mid - ph / 2f;
            using (var pen = new Pen(Color.FromArgb(a, Accent), 1.5f))
            {
                g.DrawRectangle(pen, px, pt, pw, ph);
                g.DrawLine(pen, px + 2f, pt + 4f * sc, px + pw - 2f, pt + 4f * sc);
                g.DrawLine(pen, px + 2f, pt + 8f * sc, px + pw - 3f, pt + 8f * sc);
            }
        }
    }

    static Image Fade(Image src)
    {
        var bmp = new Bitmap(src.Width, src.Height);
        using (var g = Graphics.FromImage(bmp))
        {
            var cm = new System.Drawing.Imaging.ColorMatrix { Matrix33 = 0.4f };
            using (var ia = new System.Drawing.Imaging.ImageAttributes())
            {
                ia.SetColorMatrix(cm);
                g.DrawImage(src, new Rectangle(0, 0, src.Width, src.Height),
                    0, 0, src.Width, src.Height, GraphicsUnit.Pixel, ia);
            }
        }
        return bmp;
    }
}

// Snapshot of what the aware-launcher overlays draw. Rebuilt wholesale on a
// background thread; the UI only ever reads the latest instance.
class HubStatus
{
    public bool AppUp;
    public long? MissingTotal;      // null = no/unreadable system_status.json
    public int PendingCount = -1;   // -1 = Pending Deletion folder not found
    public bool AppStale;
}

// ---- generated\file_queue_plan.json (written by the app's file_queue.py --plan)
// Fields are populated by JavaScriptSerializer via reflection - the compiler
// can't see that, hence the disable.
#pragma warning disable 0649
class FilePlan
{
    public string generated, archive_root, queue_root, note;
    public int stale_count;
    public FilePlanItem[] items;
    public FilePlanUnmatchedItem[] unmatched;   // queue files that ARE there but TMDB couldn't confirm - never auto-filed, but not "nothing to do" either
    public FilePlanJunkItem[] junk;   // added by the app 2026-09-05, additive
    public int junk_count;
    // added by the app 2026-09-05: true when something under the queue
    // root is newer than media_db.json itself - the plan is still built
    // the same way, this just says the result might be behind reality.
    public bool data_stale;
    public string media_db_refreshed_at, newest_queue_mtime;
}

class FilePlanItem
{
    public string src_video, src_entry_path, show_title, show_key, type, category,
                  dest_dir, dest_name, dest_full, retention, episode_title;
    public int? tmdb_id, season, episode;
    public long size_bytes;
    public bool default_selected;
    public Dictionary<string, object> collision;   // null, or { action, ... }
    public bool companion_pending;   // added by the app 2026-09-05, additive
}

class FilePlanJunkItem
{
    public string path, name;
    public long size_bytes;
}

class FilePlanUnmatchedItem
{
    public string src, show_title, reason;
}
#pragma warning restore 0649

// The "File" picker: a checklist of the plan's items, defaulting to each
// item's own default_selected. On OK, Selected holds the chosen src_video
// paths for `file_queue.py --execute`.
class FilePickerDialog : Form
{
    public readonly List<string> Selected = new List<string>();
    public bool SweepJunk;
    readonly FilePlanItem[] _items;
    readonly DataGridView _grid;
    readonly Label _summary;
    bool[] _locked;   // true = a keep_existing collision row; can never actually move, forced unchecked

    public FilePickerDialog(FilePlan plan)
    {
        _items = plan.items;

        Text = "File the download queue";
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(960, 600);
        BackColor = ColorTranslator.FromHtml("#0C1018");
        ForeColor = Vortex.Plain;
        Font = Vortex.SpaceFont(10F, FontStyle.Regular);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MinimizeBox = false;
        MaximizeBox = false;

        var header = new Label
        {
            Dock = DockStyle.Top,
            Height = plan.data_stale ? 62 : 48,
            Padding = new Padding(16, 12, 16, 4),
            ForeColor = plan.data_stale ? Vortex.Folder : Vortex.Muted,
            Font = Vortex.SpaceFont(9.5F, FontStyle.Regular),
            Text = _items.Length + " file(s) ready to file into " + (plan.archive_root ?? "the library")
                 + (plan.stale_count > 0
                    ? "\n" + plan.stale_count + " empty leftover folders ignored (already filed - a library refresh clears them)."
                    : "")
                 + (plan.data_stale
                    ? "\nThe app's data may be behind the queue (last refresh " + Vortex.FormatWhen(plan.media_db_refreshed_at)
                      + ") - refresh the app if something looks missing."
                    : ""),
        };

        _grid = new DataGridView
        {
            Dock = DockStyle.Fill,
            AllowUserToAddRows = false,
            AllowUserToDeleteRows = false,
            AllowUserToResizeRows = false,
            RowHeadersVisible = false,
            MultiSelect = false,
            SelectionMode = DataGridViewSelectionMode.FullRowSelect,
            AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill,
            EnableHeadersVisualStyles = false,
            BackgroundColor = ColorTranslator.FromHtml("#0C1018"),
            GridColor = ColorTranslator.FromHtml("#1E2836"),
            BorderStyle = BorderStyle.None,
            ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing,
            ColumnHeadersHeight = 30,
        };
        _grid.DefaultCellStyle.BackColor = ColorTranslator.FromHtml("#0C1018");
        _grid.DefaultCellStyle.ForeColor = Vortex.Plain;
        _grid.DefaultCellStyle.SelectionBackColor = ColorTranslator.FromHtml("#16324A");
        _grid.DefaultCellStyle.SelectionForeColor = Vortex.Plain;
        _grid.ColumnHeadersDefaultCellStyle.BackColor = ColorTranslator.FromHtml("#131A26");
        _grid.ColumnHeadersDefaultCellStyle.ForeColor = Vortex.Muted;
        _grid.ColumnHeadersDefaultCellStyle.SelectionBackColor = ColorTranslator.FromHtml("#131A26");

        _grid.Columns.Add(new DataGridViewCheckBoxColumn { HeaderText = "", FillWeight = 5, Resizable = DataGridViewTriState.False });
        _grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Show", FillWeight = 22, ReadOnly = true });
        _grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Episode / type", FillWeight = 24, ReadOnly = true });
        _grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Size", FillWeight = 9, ReadOnly = true });
        _grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Destination", FillWeight = 24, ReadOnly = true });
        _grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Flags", FillWeight = 16, ReadOnly = true });
        _grid.Columns[3].DefaultCellStyle.Alignment = DataGridViewContentAlignment.MiddleRight;

        _locked = new bool[_items.Length];
        for (int i = 0; i < _items.Length; i++)
        {
            var it = _items[i];
            _locked[i] = IsKeepExisting(it);
            int r = _grid.Rows.Add(!_locked[i] && it.default_selected, it.show_title ?? "?", Detail(it),
                                   HumanSize(it.size_bytes), it.dest_full ?? "", Flags(it));
            var gridRow = _grid.Rows[r];
            if (_locked[i])
            {
                // file_queue.py always keeps the existing copy for a
                // keep_existing collision - this row can never actually
                // move no matter what's checked. Force it unchecked and
                // non-interactive, and greyed out entirely, so it reads as
                // "already handled, nothing to do" rather than a live,
                // checked-by-default candidate (which is what it looked
                // like before this fix - a real, confusing bug: the plan
                // already says default_selected:false for these, but nothing
                // downstream of that value visibly disabled/explained them).
                gridRow.Cells[0].Value = false;
                gridRow.Cells[0].ReadOnly = true;
                gridRow.DefaultCellStyle.ForeColor = Vortex.Muted;
                gridRow.DefaultCellStyle.SelectionForeColor = Vortex.Muted;
            }
            else if (it.collision != null) gridRow.Cells[5].Style.ForeColor = Vortex.Danger;
            else if (it.companion_pending) gridRow.Cells[5].Style.ForeColor = Vortex.Orange;
            else if (it.retention == "delete_after_watch") gridRow.Cells[5].Style.ForeColor = Vortex.Folder;
        }

        _grid.CurrentCellDirtyStateChanged += (s, e) =>
        {
            if (_grid.IsCurrentCellDirty) _grid.CommitEdit(DataGridViewDataErrorContexts.Commit);
        };
        _grid.CellValueChanged += (s, e) => UpdateSummary();

        // Explicit pixel layout for everything below the grid, not Dock -
        // Dock=Right was stretching the buttons to fill the row's height,
        // which is what made this bar look cramped/"cut off" before.
        bool hasJunk = plan.junk_count > 0;
        int barHeight = hasJunk ? 104 : 64;
        var bar = new Panel { Dock = DockStyle.Bottom, Height = barHeight, BackColor = ColorTranslator.FromHtml("#0C1018") };

        CheckBox junkCheck = null;
        if (hasJunk)
        {
            long junkBytes = 0;
            if (plan.junk != null) foreach (var j in plan.junk) junkBytes += j.size_bytes;
            junkCheck = new CheckBox
            {
                Location = new Point(16, 12),
                Size = new Size(ClientSize.Width - 32, 26),
                Checked = true,
                BackColor = ColorTranslator.FromHtml("#0C1018"),
                ForeColor = Vortex.Folder,
                Text = "Also move " + plan.junk_count + " junk/empty folder(s) to Pending Deletion ("
                     + HumanSize(junkBytes) + ") - no real video in them",
            };
            bar.Controls.Add(junkCheck);
        }

        int btnRowY = barHeight - 46;
        _summary = new Label
        {
            Location = new Point(16, btnRowY + 6),
            Size = new Size(340, 24),
            TextAlign = ContentAlignment.MiddleLeft,
            ForeColor = Vortex.Muted,
        };
        bar.Controls.Add(_summary);

        var btnOk = MkBtn("File selected", () => { Collect(); SweepJunk = junkCheck != null && junkCheck.Checked; DialogResult = DialogResult.OK; Close(); });
        btnOk.ForeColor = Vortex.Primary;
        btnOk.FlatAppearance.BorderColor = Vortex.Primary;
        var btnCancel = MkBtn("Cancel", () => { DialogResult = DialogResult.Cancel; Close(); });
        var btnDef = MkBtn("Default", () =>
        {
            for (int i = 0; i < _items.Length; i++)
                _grid.Rows[i].Cells[0].Value = !_locked[i] && _items[i].default_selected;
        });
        var btnNone = MkBtn("None", () => SetAll(false));
        var btnAll = MkBtn("All", () => SetAll(true));

        int bx = ClientSize.Width - 16;
        foreach (var b in new[] { btnOk, btnCancel, btnDef, btnNone, btnAll })
        {
            bx -= b.Width;
            b.Location = new Point(bx, btnRowY);
            bar.Controls.Add(b);
            bx -= 8;
        }

        Controls.Add(_grid);
        Controls.Add(bar);
        Controls.Add(header);
        AcceptButton = btnOk;
        CancelButton = btnCancel;
        UpdateSummary();
    }

    Button MkBtn(string text, Action onClick)
    {
        var b = new Button
        {
            Text = text,
            Width = 104,
            Height = 32,
            FlatStyle = FlatStyle.Flat,
            BackColor = ColorTranslator.FromHtml("#151D2A"),
            ForeColor = Vortex.Plain,
            Margin = new Padding(6, 0, 0, 0),
        };
        b.FlatAppearance.BorderColor = ColorTranslator.FromHtml("#2A3446");
        b.Click += (s, e) => onClick();
        return b;
    }

    void SetAll(bool v)
    {
        for (int i = 0; i < _grid.Rows.Count; i++)
            if (!_locked[i]) _grid.Rows[i].Cells[0].Value = v;
    }

    void Collect()
    {
        Selected.Clear();
        for (int i = 0; i < _items.Length; i++)
            if (Convert.ToBoolean(_grid.Rows[i].Cells[0].Value ?? false)) Selected.Add(_items[i].src_video);
    }

    void UpdateSummary()
    {
        long n = 0, bytes = 0;
        for (int i = 0; i < _items.Length; i++)
            if (Convert.ToBoolean(_grid.Rows[i].Cells[0].Value ?? false)) { n++; bytes += _items[i].size_bytes; }
        _summary.Text = n + " of " + _items.Length + " selected   \u00b7   " + HumanSize(bytes);
    }

    static string Detail(FilePlanItem it)
    {
        if (it.season.HasValue && it.episode.HasValue)
        {
            string e = string.Format("S{0:00}E{1:00}", it.season.Value, it.episode.Value);
            return string.IsNullOrEmpty(it.episode_title) ? e : e + "  " + it.episode_title;
        }
        return it.type ?? "";
    }

    static bool IsKeepExisting(FilePlanItem it)
    {
        if (it.collision == null) return false;
        object a;
        return it.collision.TryGetValue("action", out a) && a != null && a.ToString() == "keep_existing";
    }

    static string Flags(FilePlanItem it)
    {
        var parts = new List<string>();
        if (it.companion_pending) parts.Add("companion tool pending (unchecked)");
        if (it.retention == "delete_after_watch") parts.Add("delete-after-watch");
        if (it.collision != null)
        {
            object a;
            string act = it.collision.TryGetValue("action", out a) && a != null ? a.ToString() : "collision";
            parts.Add(act == "keep_existing" ? "already filed - no action needed" : "replaces older copy");
        }
        return string.Join("  \u00b7  ", parts);
    }

    static string HumanSize(long b)
    {
        if (b <= 0) return "";
        double gb = b / 1073741824.0;
        return gb >= 1 ? gb.ToString("0.0") + " GB" : (b / 1048576.0).ToString("0") + " MB";
    }
}

// ---- generated\rename_plan.json (rename_queue_inplace.py --plan) --------
#pragma warning disable 0649
class RenamePlan
{
    public string generated_at;
    public string[] queue_roots;
    public RenamePlanItem[] ready;         // has proposed_name, flags empty
    public RenamePlanItem[] needs_review;  // proposed_name null, a reason + flags + alternatives
}

class RenamePlanItem
{
    public string src_path, release_folder, show_title, type,
                  ep_name, proposed_name, proposed_folder, reason;
    public int? tmdb_id, season, episode;
    public string[] flags;
    public RenameAlt[] alternatives;       // populated only for reboot_ambiguous
}

class RenameAlt
{
    public string ep_name, proposed_name, source;   // source e.g. "season_offset 7"
    public int? tmdb_id, season, episode;
}
#pragma warning restore 0649

// The "Rename" picker. One combo per row:
//   ready rows      -> ["Rename -> <name>", "Skip"]              default Rename
//   needs-review rows-> ["Leave - needs review", <alt>, <alt>...] default Leave
// On OK, Selection holds {src_path, name} for rename_queue_inplace --apply.
class RenamePickerDialog : Form
{
    public readonly List<Dictionary<string, object>> Selection = new List<Dictionary<string, object>>();

    class Row { public string SrcPath; public bool IsReady; public string ReadyName; public RenameAlt[] Alts; public string[] Options; }
    readonly Row[] _rows;
    readonly DataGridView _grid;
    readonly Label _summary;

    public RenamePickerDialog(RenamePlan plan)
    {
        var ready = plan.ready ?? new RenamePlanItem[0];
        var review = plan.needs_review ?? new RenamePlanItem[0];
        _rows = new Row[ready.Length + review.Length];

        Text = "Rename the download queue";
        StartPosition = FormStartPosition.CenterScreen;
        ClientSize = new Size(1000, 560);
        BackColor = ColorTranslator.FromHtml("#0C1018");
        ForeColor = Vortex.Plain;
        Font = Vortex.SpaceFont(10F, FontStyle.Regular);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        MinimizeBox = false;
        MaximizeBox = false;

        var header = new Label
        {
            Dock = DockStyle.Top,
            Height = review.Length > 0 ? 60 : 44,
            Padding = new Padding(16, 12, 16, 4),
            ForeColor = review.Length > 0 ? Vortex.Folder : Vortex.Muted,
            Font = Vortex.SpaceFont(9.5F, FontStyle.Regular),
            Text = ready.Length + " ready to rename"
                 + (review.Length > 0
                    ? ",  " + review.Length + " need a decision"
                      + "\nReview rows look like a reboot / season-numbering mismatch - pick the right reading, or leave it and fix the offset in the app Admin."
                    : "  -  confirm and apply."),
        };

        _grid = new DataGridView
        {
            Dock = DockStyle.Fill,
            AllowUserToAddRows = false,
            AllowUserToDeleteRows = false,
            AllowUserToResizeRows = false,
            RowHeadersVisible = false,
            MultiSelect = false,
            SelectionMode = DataGridViewSelectionMode.FullRowSelect,
            AutoSizeColumnsMode = DataGridViewAutoSizeColumnsMode.Fill,
            EnableHeadersVisualStyles = false,
            BackgroundColor = ColorTranslator.FromHtml("#0C1018"),
            GridColor = ColorTranslator.FromHtml("#1E2836"),
            BorderStyle = BorderStyle.None,
            ColumnHeadersHeightSizeMode = DataGridViewColumnHeadersHeightSizeMode.DisableResizing,
            ColumnHeadersHeight = 30,
            EditMode = DataGridViewEditMode.EditOnEnter,
        };
        _grid.DefaultCellStyle.BackColor = ColorTranslator.FromHtml("#0C1018");
        _grid.DefaultCellStyle.ForeColor = Vortex.Plain;
        _grid.DefaultCellStyle.SelectionBackColor = ColorTranslator.FromHtml("#16324A");
        _grid.DefaultCellStyle.SelectionForeColor = Vortex.Plain;
        _grid.ColumnHeadersDefaultCellStyle.BackColor = ColorTranslator.FromHtml("#131A26");
        _grid.ColumnHeadersDefaultCellStyle.ForeColor = Vortex.Muted;
        _grid.ColumnHeadersDefaultCellStyle.SelectionBackColor = ColorTranslator.FromHtml("#131A26");
        _grid.DataError += (s, e) => { e.ThrowException = false; };

        _grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Show", FillWeight = 18, ReadOnly = true });
        _grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "In the queue as", FillWeight = 16, ReadOnly = true });
        _grid.Columns.Add(new DataGridViewTextBoxColumn { HeaderText = "Note", FillWeight = 30, ReadOnly = true });
        _grid.Columns.Add(new DataGridViewComboBoxColumn
        {
            HeaderText = "Rename to",
            FillWeight = 36,
            FlatStyle = FlatStyle.Flat,
            DisplayStyle = DataGridViewComboBoxDisplayStyle.DropDownButton,
        });

        int ri = 0;
        foreach (var it in ready)
        {
            var opts = new[] { "Rename → " + (it.proposed_name ?? "?"), "Skip" };
            _rows[ri] = new Row { SrcPath = it.src_path, IsReady = true, ReadyName = it.proposed_name, Options = opts };
            AddRow(it, "", opts, 0, false);
            ri++;
        }
        foreach (var it in review)
        {
            var alts = it.alternatives ?? new RenameAlt[0];
            var opts = new string[1 + alts.Length];
            opts[0] = "Leave — needs review";
            for (int k = 0; k < alts.Length; k++) opts[k + 1] = AltLabel(alts[k]);
            _rows[ri] = new Row { SrcPath = it.src_path, IsReady = false, Alts = alts, Options = opts };
            AddRow(it, it.reason ?? "needs review", opts, 0, true);
            ri++;
        }

        _grid.CurrentCellDirtyStateChanged += (s, e) =>
        {
            if (_grid.IsCurrentCellDirty) _grid.CommitEdit(DataGridViewDataErrorContexts.Commit);
        };
        _grid.CellValueChanged += (s, e) => UpdateSummary();

        var bar = new Panel { Dock = DockStyle.Bottom, Height = 64, BackColor = ColorTranslator.FromHtml("#0C1018") };
        int btnRowY = 18;
        _summary = new Label
        {
            Location = new Point(16, btnRowY + 6),
            Size = new Size(380, 24),
            TextAlign = ContentAlignment.MiddleLeft,
            ForeColor = Vortex.Muted,
        };
        bar.Controls.Add(_summary);

        var btnOk = MkBtn("Apply", () => { Collect(); DialogResult = DialogResult.OK; Close(); });
        btnOk.ForeColor = Vortex.Primary;
        btnOk.FlatAppearance.BorderColor = Vortex.Primary;
        var btnCancel = MkBtn("Cancel", () => { DialogResult = DialogResult.Cancel; Close(); });
        var btnDef = MkBtn("Default", () => SetReady(0));    // ready -> Rename, review -> Leave
        var btnNone = MkBtn("Skip all", () => SetReady(1));  // ready -> Skip

        int bx = ClientSize.Width - 16;
        foreach (var b in new[] { btnOk, btnCancel, btnDef, btnNone })
        {
            bx -= b.Width;
            b.Location = new Point(bx, btnRowY);
            bar.Controls.Add(b);
            bx -= 8;
        }

        Controls.Add(_grid);
        Controls.Add(bar);
        Controls.Add(header);
        AcceptButton = btnOk;
        CancelButton = btnCancel;
        UpdateSummary();
    }

    void AddRow(RenamePlanItem it, string note, string[] opts, int defaultIdx, bool review)
    {
        string queueAs = it.season.HasValue && it.episode.HasValue
            ? string.Format("S{0:00}E{1:00}", it.season.Value, it.episode.Value)
            : (it.release_folder != null ? System.IO.Path.GetFileName(it.release_folder.TrimEnd('\\', '/')) : "");
        int r = _grid.Rows.Add(it.show_title ?? "?", queueAs, note, null);
        var cell = (DataGridViewComboBoxCell)_grid.Rows[r].Cells[3];
        cell.Items.AddRange(opts);
        cell.Value = opts[Math.Min(defaultIdx, opts.Length - 1)];
        if (review)
        {
            _grid.Rows[r].Cells[2].Style.ForeColor = Vortex.Folder;
        }
    }

    static string AltLabel(RenameAlt a)
    {
        string se = a.season.HasValue && a.episode.HasValue
            ? string.Format("S{0:00}E{1:00}", a.season.Value, a.episode.Value) : "?";
        string t = string.IsNullOrEmpty(a.ep_name) ? "" : "  " + a.ep_name;
        string src = string.IsNullOrEmpty(a.source) ? "" : "   [" + a.source + "]";
        return se + t + src;
    }

    Button MkBtn(string text, Action onClick)
    {
        var b = new Button
        {
            Text = text,
            Width = 104,
            Height = 32,
            FlatStyle = FlatStyle.Flat,
            BackColor = ColorTranslator.FromHtml("#151D2A"),
            ForeColor = Vortex.Plain,
        };
        b.FlatAppearance.BorderColor = ColorTranslator.FromHtml("#2A3446");
        b.Click += (s, e) => onClick();
        return b;
    }

    void SetReady(int idx)
    {
        for (int i = 0; i < _rows.Length; i++)
        {
            if (!_rows[i].IsReady) continue;
            var cell = (DataGridViewComboBoxCell)_grid.Rows[i].Cells[3];
            cell.Value = _rows[i].Options[Math.Min(idx, _rows[i].Options.Length - 1)];
        }
        UpdateSummary();
    }

    void Collect()
    {
        Selection.Clear();
        for (int i = 0; i < _rows.Length; i++)
        {
            var row = _rows[i];
            string picked = _grid.Rows[i].Cells[3].Value as string;
            int idx = row.Options == null ? -1 : Array.IndexOf(row.Options, picked);
            if (row.IsReady)
            {
                if (idx == 0 && !string.IsNullOrEmpty(row.ReadyName))
                    Selection.Add(new Dictionary<string, object> { { "src_path", row.SrcPath }, { "name", row.ReadyName } });
            }
            else
            {
                if (idx >= 1 && row.Alts != null && idx - 1 < row.Alts.Length)
                    Selection.Add(new Dictionary<string, object> { { "src_path", row.SrcPath }, { "name", row.Alts[idx - 1].proposed_name } });
            }
        }
    }

    void UpdateSummary()
    {
        int n = 0;
        for (int i = 0; i < _rows.Length; i++)
        {
            var row = _rows[i];
            string picked = _grid.Rows[i].Cells[3].Value as string;
            int idx = row.Options == null ? -1 : Array.IndexOf(row.Options, picked);
            if (row.IsReady ? idx == 0 : idx >= 1) n++;
        }
        _summary.Text = n + " of " + _rows.Length + " will be renamed";
    }
}

// App-styled modal - replaces the stock MessageBox everywhere: no system
// chime, no monospace stdout dump. Dark, borderless, thin accent edge,
// drop shadow, draggable, Esc/Enter to close. Kind only sets the accent
// colour + which button reads as primary.
class HubDialog : Form
{
    internal enum Kind { Info, Success, Warn, Error }

    HubDialog(Kind kind, string title, string body, string primaryText, string secondaryText, string logPath)
    {
        Color accent = kind == Kind.Success ? Vortex.Safe
                     : kind == Kind.Warn ? Vortex.Folder
                     : kind == Kind.Error ? Vortex.Danger
                     : Vortex.Primary;

        FormBorderStyle = FormBorderStyle.None;
        BackColor = ColorTranslator.FromHtml("#0C1018");
        ForeColor = Vortex.Plain;
        Font = Vortex.SpaceFont(10F, FontStyle.Regular);
        ShowInTaskbar = false;
        KeyPreview = true;
        StartPosition = FormStartPosition.CenterScreen;

        const int W = 452, pad = 24;

        var titleLbl = new Label
        {
            Text = title ?? "",
            Font = Vortex.SpaceFont(13.5F, FontStyle.Bold),
            ForeColor = accent,
            AutoSize = false,
            AutoEllipsis = true,
            Location = new Point(pad, 20),
            Size = new Size(W - pad * 2, 26),
        };

        var bodySz = TextRenderer.MeasureText(body ?? "", Font,
            new Size(W - pad * 2, 4000), TextFormatFlags.WordBreak | TextFormatFlags.TextBoxControl);
        var bodyLbl = new Label
        {
            Text = body ?? "",
            AutoSize = false,
            Location = new Point(pad, 52),
            Size = new Size(W - pad * 2, Math.Max(18, bodySz.Height + 2)),
            ForeColor = Vortex.Plain,
        };

        int y = bodyLbl.Bottom + 12;

        Label logLbl = null;
        if (!string.IsNullOrEmpty(logPath) && File.Exists(logPath))
        {
            string lp = logPath;
            logLbl = new Label
            {
                Text = "View log ›",
                Font = Vortex.SpaceFont(9F, FontStyle.Regular),
                ForeColor = Vortex.Muted,
                AutoSize = true,
                Cursor = Cursors.Hand,
                Location = new Point(pad, y),
            };
            logLbl.Click += (s, e) => { try { Process.Start(new ProcessStartInfo(lp) { UseShellExecute = true }); } catch { } };
            y = logLbl.Bottom + 10;
        }

        int btnY = y + 6;
        var primary = MkBtn(primaryText ?? "OK", accent, true, () => { DialogResult = DialogResult.OK; Close(); });
        int bx = W - pad - primary.Width;
        primary.Location = new Point(bx, btnY);

        Button secondary = null;
        if (secondaryText != null)
        {
            secondary = MkBtn(secondaryText, Vortex.Muted, false, () => { DialogResult = DialogResult.Cancel; Close(); });
            bx -= 10 + secondary.Width;
            secondary.Location = new Point(bx, btnY);
        }

        ClientSize = new Size(W, btnY + primary.Height + 20);

        Controls.Add(titleLbl);
        Controls.Add(bodyLbl);
        if (logLbl != null) Controls.Add(logLbl);
        Controls.Add(primary);
        if (secondary != null) Controls.Add(secondary);

        AcceptButton = primary;
        CancelButton = secondary ?? primary;
        Vortex.MakeDraggable(this, this);
        KeyDown += (s, e) =>
        {
            if (e.KeyCode == Keys.Escape)
            { DialogResult = secondary != null ? DialogResult.Cancel : DialogResult.OK; Close(); }
        };
    }

    protected override CreateParams CreateParams
    {
        get { var cp = base.CreateParams; cp.ClassStyle |= 0x00020000; return cp; }  // CS_DROPSHADOW
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        using (var pen = new Pen(Color.FromArgb(130, 90, 120, 170)))
            e.Graphics.DrawRectangle(pen, 0, 0, ClientSize.Width - 1, ClientSize.Height - 1);
    }

    static Button MkBtn(string text, Color accent, bool primary, Action onClick)
    {
        var b = new Button
        {
            Text = text,
            Width = 100,
            Height = 30,
            FlatStyle = FlatStyle.Flat,
            BackColor = ColorTranslator.FromHtml(primary ? "#17222F" : "#141B26"),
            ForeColor = primary ? accent : Vortex.Plain,
        };
        b.FlatAppearance.BorderColor = primary ? accent : ColorTranslator.FromHtml("#2A3446");
        b.Click += (s, e) => onClick();
        return b;
    }

    internal static void Show(Kind kind, string title, string body) { Show(kind, title, body, null); }

    internal static void Show(Kind kind, string title, string body, string logPath)
    {
        using (var d = new HubDialog(kind, title, body, "OK", null, logPath))
        {
            var o = Form.ActiveForm;
            if (o != null && !o.IsDisposed) { d.StartPosition = FormStartPosition.CenterParent; d.ShowDialog(o); }
            else d.ShowDialog();
        }
    }

    internal static bool Confirm(string title, string body, string confirmText)
    {
        using (var d = new HubDialog(Kind.Warn, title, body, confirmText, "Cancel", null))
        {
            var o = Form.ActiveForm;
            DialogResult r;
            if (o != null && !o.IsDisposed) { d.StartPosition = FormStartPosition.CenterParent; r = d.ShowDialog(o); }
            else r = d.ShowDialog();
            return r == DialogResult.OK;
        }
    }
}

// Minimise / maximise / close glyph for the borderless window's own
// chrome. Painted, transparent, hover-lit (red wash on the close button,
// plain white wash on the rest).
class GlyphButton : Control
{
    public enum Kind { Minimize, Maximize, Close }
    public Kind Type;
    bool _hover;

    public GlyphButton()
    {
        SetStyle(ControlStyles.SupportsTransparentBackColor | ControlStyles.UserPaint
               | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
        BackColor = Color.Transparent;
        Size = new Size(30, 26);
        Cursor = Cursors.Hand;
    }

    protected override void OnMouseEnter(EventArgs e) { _hover = true; Invalidate(); base.OnMouseEnter(e); }
    protected override void OnMouseLeave(EventArgs e) { _hover = false; Invalidate(); base.OnMouseLeave(e); }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        InvokePaintBackground(this, e);
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;

        if (_hover)
        {
            Color hb = Type == Kind.Close
                ? Color.FromArgb(150, 224, 90, 78)
                : Color.FromArgb(55, 255, 255, 255);
            using (var b = new SolidBrush(hb))
            using (var p = Vortex.RoundedRectPath(new RectangleF(0, 0, Width, Height), 6))
                g.FillPath(b, p);
        }

        Color fg = _hover && Type == Kind.Close ? Color.White : Color.FromArgb(215, 208, 216, 224);
        using (var pen = new Pen(fg, 1.6f))
        {
            float cx = Width / 2f, cy = Height / 2f;
            if (Type == Kind.Close)
            {
                g.DrawLine(pen, cx - 5, cy - 5, cx + 5, cy + 5);
                g.DrawLine(pen, cx - 5, cy + 5, cx + 5, cy - 5);
            }
            else if (Type == Kind.Maximize)
            {
                g.DrawRectangle(pen, cx - 5, cy - 5, 10, 10);
            }
            else // Minimize
            {
                g.DrawLine(pen, cx - 5, cy + 4, cx + 5, cy + 4);
            }
        }
    }
}

// Quiet status line, bottom-left: the app / the companion tool up-down dots, then
// whatever counts are worth mentioning (missing episodes, files ready to
// file, Pending Deletion backlog). Nothing shows for a count that's zero
// or unknown; stale sources read dimmer.
// One line of small text, centred, drawn straight onto the background. Fades in when a job
// starts, fades out when it ends; an outcome line holds for a few seconds first.
class CaptionLabel : Control
{
    string _text = "";
    float _alpha, _target;
    DateTime _holdUntil = DateTime.MaxValue;
    readonly Timer _timer;

    public CaptionLabel()
    {
        SetStyle(ControlStyles.SupportsTransparentBackColor | ControlStyles.UserPaint
               | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
        BackColor = Color.Transparent;
        Size = new Size(900, 22);
        TabStop = false;
        _timer = new Timer { Interval = 33 };
        _timer.Tick += (s, e) => Step();
    }

    public void Working(string text) { _holdUntil = DateTime.MaxValue; Set(text); }
    public void Result(string text, int seconds) { _holdUntil = DateTime.UtcNow.AddSeconds(seconds); Set(text); }
    public void Clear() { _target = 0f; _timer.Start(); }

    void Set(string text) { _text = text; _target = 1f; _timer.Start(); Invalidate(); }

    void Step()
    {
        if (_target > 0f && DateTime.UtcNow > _holdUntil) _target = 0f;
        float before = _alpha;
        _alpha += (_target - _alpha) * 0.22f;
        if (Math.Abs(_target - _alpha) < 0.02f) _alpha = _target;
        if (Math.Abs(_alpha - before) > 0.002f) Invalidate();
        if (_alpha == 0f && _target == 0f) _timer.Stop();
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        InvokePaintBackground(this, e);
        if (_alpha <= 0.01f || string.IsNullOrEmpty(_text)) return;
        g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAlias;
        using (var f = Vortex.SpaceFont(9F, FontStyle.Regular))
        using (var b = new SolidBrush(Color.FromArgb((int)(215 * _alpha), Vortex.TextColor)))
        using (var sf = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center,
                                           Trimming = StringTrimming.EllipsisCharacter, FormatFlags = StringFormatFlags.NoWrap })
            g.DrawString(_text, f, b, new RectangleF(0, 0, Width, Height), sf);
    }
}

class StatusStrip : Control
{
    public StatusStrip()
    {
        SetStyle(ControlStyles.SupportsTransparentBackColor | ControlStyles.UserPaint
               | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
        BackColor = Color.Transparent;
        Size = new Size(640, 22);
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        InvokePaintBackground(this, e);
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;
        g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.AntiAlias;

        var st = Vortex.Status;
        using (var f = Vortex.SpaceFont(9F, FontStyle.Regular))
        {
            float x = 0f, cy = Height / 2f;
            x = Dot(g, f, x, cy, st.AppUp, Vortex.AppDisplayName);
            x += 26f;

            // Always shown, zero included. "-" means the source file/folder
            // wasn't readable (distinct from a real 0).
            string miss = st.MissingTotal.HasValue ? st.MissingTotal.Value.ToString() : "-";
            string pend = st.PendingCount >= 0 ? st.PendingCount.ToString() : "-";
            string counts = "missing " + miss + "   ·   pending " + pend;
            bool dim = st.AppStale;
            // TextColor, not Muted grey (2026-09-13, at the user's request)
            // - same reasoning as the header's "MEDIA CONTROL": a genuinely
            // brighter color reads better than a lightened grey.
            TextRenderer.DrawText(g, counts, f,
                new Rectangle((int)x, 0, Width - (int)x, Height),
                Color.FromArgb(dim ? 140 : 220, Vortex.TextColor),
                TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPadding);
        }
    }

    float Dot(Graphics g, Font f, float x, float cy, bool up, string label)
    {
        Color c = up ? Vortex.Safe : Color.FromArgb(150, 128, 136, 148);
        using (var b = new SolidBrush(c))
            g.FillEllipse(b, x, cy - 3.5f, 7f, 7f);
        var lr = new Rectangle((int)x + 12, 0, 220, Height);
        TextRenderer.DrawText(g, label, f, lr, Color.FromArgb(210, Vortex.TextColor),
            TextFormatFlags.Left | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPadding);
        var ls = TextRenderer.MeasureText(g, label, f, Size.Empty, TextFormatFlags.NoPadding);
        return x + 12 + ls.Width;
    }
}

// The the app launch element: the portal art with a slow rotating ring
// painted behind it, all in one control so the ring composites correctly
// against the background image. The ring's angle is advanced by the form's
// shared animation timer (which is paused when the window isn't active, so
// this costs nothing in the background).
class PortalButton : Button
{
    public Image Art;
    public float Angle;
    bool _hover;

    // Particle field - sparks streaming out of the vortex. Simple parallel
    // arrays, stepped from OnPaint using a dt derived from Fx.Clock (the
    // portal repaints every frame). All inside this 320x320 control, so no
    // window repaint = no flicker.
    const int NP = 44;
    float[] _pa, _pr, _ps, _pl;   // angle, radius, speed, life(1->0)
    int[] _phue;
    Random _prng;
    float _pClock = -1f;

    public PortalButton()
    {
        SetStyle(ControlStyles.SupportsTransparentBackColor | ControlStyles.UserPaint
               | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
        BackColor = Color.Transparent;
        FlatStyle = FlatStyle.Flat;
        FlatAppearance.BorderSize = 0;
        Cursor = Cursors.Hand;
    }

    public bool Hovered { get { return _hover; } }

    protected override bool ShowFocusCues { get { return false; } }
    protected override void OnMouseEnter(EventArgs e) { _hover = true; Invalidate(); base.OnMouseEnter(e); }
    protected override void OnMouseLeave(EventArgs e) { _hover = false; Invalidate(); base.OnMouseLeave(e); }

    protected override void OnPaint(PaintEventArgs e)
    {
        var g = e.Graphics;
        InvokePaintBackground(this, e);
        g.SmoothingMode = System.Drawing.Drawing2D.SmoothingMode.AntiAlias;

        float cx = Width / 2f, cy = Height / 2f;

        // Ambient vortex tunnel - faint rings turning out from the eye,
        // counter to the ring. Lives here (the portal repaints itself every
        // frame in a double-buffered DC) rather than on the form background,
        // where a per-frame window repaint flickered the header.
        if (Vortex.Fx.Tunnel)
        {
            var ts = g.Save();
            g.TranslateTransform(cx, cy);
            g.RotateTransform(-Vortex.Fx.Clock * (7f + 50f * Vortex.Fx.Warp));
            DrawTunnel(g);
            g.Restore(ts);
        }

        // Outer rainbow ring turns one way at Angle...
        var save = g.Save();
        g.TranslateTransform(cx, cy);
        g.RotateTransform(Angle);
        DrawRing(g);
        g.Restore(save);

        // ...the inner detail ring turns the OTHER way, a bit faster - real
        // vortices have differential rotation, and it fixes the inner ring
        // reading as a static orange/blue smear.
        var s2 = g.Save();
        g.TranslateTransform(cx, cy);
        g.RotateTransform(-Angle * 1.7f);
        DrawInnerRing(g);
        g.Restore(s2);

        if (Art != null)
            g.DrawImage(Art, (int)(cx - Art.Width / 2f), (int)(cy - Art.Height / 2f), Art.Width, Art.Height);

        // Sparks + pulse ripple drawn LAST, on top of the art, so they're
        // actually visible (the old pulse was under the 200px art and far
        // too faint).
        float now = Vortex.Fx.Clock;
        float dt = _pClock < 0f ? 0.016f : now - _pClock;
        _pClock = now;
        if (dt <= 0f || dt > 0.1f) dt = 0.016f;

        StepParticles(dt);
        DrawParticles(g, cx, cy);
        DrawPulse(g, cx, cy);
        // no base.OnPaint - it would draw the stock button over this
    }

    void EnsureParticles()
    {
        if (_pa != null) return;
        _prng = new Random(1234);
        _pa = new float[NP]; _pr = new float[NP]; _ps = new float[NP];
        _pl = new float[NP]; _phue = new int[NP];
        for (int i = 0; i < NP; i++) { Respawn(i); _pr[i] = 20f + (float)_prng.NextDouble() * 140f; }
    }

    void Respawn(int i)
    {
        _pa[i] = (float)(_prng.NextDouble() * Math.PI * 2.0);
        _pr[i] = 14f + (float)_prng.NextDouble() * 10f;
        _ps[i] = 26f + (float)_prng.NextDouble() * 70f;
        _pl[i] = 1f;
        _phue[i] = _prng.Next(3) == 0 ? _prng.Next(Vortex.TitleLetterColors.Length) : -1;  // colour sparks roughly 1-in-3 now, was 1-in-10
    }

    void StepParticles(float dt)
    {
        EnsureParticles();
        float boost = 1f + Vortex.Fx.Warp * 6f + Vortex.Fx.PortalCharge * 3f;
        float edge = Width * 0.5f - 4f;
        for (int i = 0; i < NP; i++)
        {
            _pr[i] += _ps[i] * dt * boost;
            _pa[i] += (0.35f + 40f / (_pr[i] + 20f)) * dt;   // swirl - tighter near the eye
            _pl[i] -= dt * (0.28f + 0.5f * Vortex.Fx.Warp);
            if (_pl[i] <= 0f || _pr[i] >= edge) Respawn(i);
        }
    }

    void DrawParticles(Graphics g, float cx, float cy)
    {
        if (_pa == null) return;
        for (int i = 0; i < NP; i++)
        {
            float x = cx + (float)Math.Cos(_pa[i]) * _pr[i];
            float y = cy + (float)Math.Sin(_pa[i]) * _pr[i];
            float fade = _pl[i] * _pl[i];
            int a = (int)(210f * fade);
            if (a < 6) continue;
            Color c = _phue[i] < 0 ? Color.FromArgb(a, 210, 232, 255)
                                   : Color.FromArgb(a, Vortex.TitleLetterColors[_phue[i]]);
            float s = 1.1f + 1.6f * fade;
            using (var glow = new SolidBrush(Color.FromArgb(a / 3, c)))
                g.FillEllipse(glow, x - s * 2.2f, y - s * 2.2f, s * 4.4f, s * 4.4f);
            using (var core = new SolidBrush(c))
                g.FillEllipse(core, x - s, y - s, s * 2f, s * 2f);
        }
    }

    void DrawPulse(Graphics g, float cx, float cy)
    {
        float period = 7.5f;
        float ph = (Vortex.Fx.Clock % period) / period;
        if (ph > 0.8f) return;
        float t = ph / 0.8f;
        float rr = 24f + t * 150f;
        int a = (int)(150f * (1f - t) * (1f - t));
        if (a < 4) return;
        var s = g.Save();
        g.TranslateTransform(cx, cy);
        using (var pen = new Pen(Color.FromArgb(a, 220, 236, 255), 2.4f))
            g.DrawEllipse(pen, -rr, -rr, rr * 2f, rr * 2f);
        using (var pen2 = new Pen(Color.FromArgb(a / 2, 180, 210, 255), 6f))
            g.DrawEllipse(pen2, -rr, -rr, rr * 2f, rr * 2f);
        g.Restore(s);
    }

    void DrawTunnel(Graphics g)
    {
        var pal = Vortex.TitleLetterColors;
        float warp = Vortex.Fx.Warp;
        float maxR = Width * 0.62f;
        int rings = 7;
        float drift = (Vortex.Fx.Clock * 0.07f) % 1f;
        for (int i = 0; i < rings; i++)
        {
            float t = (i + drift) / rings;
            float rr = t * maxR;
            if (rr < 14f) continue;
            int a = (int)(26f * (1f - t) + 60f * warp);
            if (a <= 2) continue;
            using (var pen = new Pen(Color.FromArgb(Math.Min(140, a), pal[i % pal.Length]), 1.5f + 3.5f * warp))
            {
                pen.DashStyle = System.Drawing.Drawing2D.DashStyle.Dash;
                g.DrawEllipse(pen, -rr, -rr, rr * 2f, rr * 2f);
            }
        }
        if (warp > 0.02f)
            using (var pen = new Pen(Color.FromArgb((int)(160 * warp), 225, 238, 255), 1.4f))
                for (int i = 0; i < 20; i++)
                {
                    double ang = i * (Math.PI * 2.0 / 20.0);
                    float x0 = (float)Math.Cos(ang), y0 = (float)Math.Sin(ang);
                    g.DrawLine(pen, x0 * 40f, y0 * 40f, x0 * maxR, y0 * maxR);
                }
    }

    void DrawRing(Graphics g)
    {
        var pal = Vortex.RingColors;   // muted version of the wordmark's rainbow - see RingColors comment
        // Reactive layer: hovering a launch button "pulls" the ring bright;
        // a launch fires a white charge flare; Ctrl+Shift+V warps it hard.
        float pull   = Vortex.Fx.ReactiveRing ? Vortex.Fx.PortalPull   : 0f;
        float charge = Vortex.Fx.ReactiveRing ? Vortex.Fx.PortalCharge : 0f;
        float warp   = Vortex.Fx.Warp;
        bool h = _hover || pull > 0.2f;
        // Fixed radius sized to the ~200px art (r ~= 100), NOT to the
        // control - so growing the control for clip-margin never bloats
        // the ring. Halo reaches ~r+26; keep the control's half-size well
        // above that (300/2 = 150).
        float r = 116f;
        float seg = 360f / pal.Length;
        var round = System.Drawing.Drawing2D.LineCap.Round;

        // whole-ring bloom, two passes. GlowMul (2026-09-12, at the user's
        // request) turns down just the soft haze - the opaque core arc and
        // white hot-line right below are the ring's actual definition and
        // stay at full intensity.
        const float GlowMul = 0.7f;
        using (var b1 = new Pen(Color.FromArgb((int)((h ? 72 : 52) * GlowMul), 190, 210, 255), 26f))
            g.DrawEllipse(b1, -r, -r, r * 2, r * 2);
        using (var b2 = new Pen(Color.FromArgb((int)((h ? 95 : 70) * GlowMul), 214, 228, 255), 12f))
            g.DrawEllipse(b2, -r, -r, r * 2, r * 2);

        // OUTER rainbow: 4-layer halo + opaque core + white-hot centre line
        for (int i = 0; i < pal.Length; i++)
        {
            float a0 = i * seg + 4f, sweep = seg - 10f;
            Color c = pal[i];
            for (int k = 0; k < 4; k++)
                using (var gp = new Pen(Color.FromArgb(Math.Max(8, (int)((h ? 98 : 74) * GlowMul) - k * 16), c), (h ? 14f : 11f) + k * 6f))
                {
                    gp.StartCap = gp.EndCap = round;
                    g.DrawArc(gp, -r, -r, r * 2, r * 2, a0, sweep);
                }
            using (var cp = new Pen(Color.FromArgb(255, c), 4f))
            {
                cp.StartCap = cp.EndCap = round;
                g.DrawArc(cp, -r, -r, r * 2, r * 2, a0, sweep);
            }
            using (var hot = new Pen(Color.FromArgb(h ? 175 : 135, 255, 255, 255), 1.4f))
            {
                hot.StartCap = hot.EndCap = round;
                g.DrawArc(hot, -r, -r, r * 2, r * 2, a0 + 2f, sweep - 4f);
            }
        }

        // ---- reactive overlays (only when something's driving them) ----
        float boost = pull * 0.45f + charge * 1.3f + warp * 1.7f;
        if (boost > 0.01f)
        {
            float bb = Math.Min(2.4f, boost);
            for (int i = 0; i < pal.Length; i++)
            {
                float a0 = i * seg + 4f, sweep = seg - 10f;
                using (var gp = new Pen(Color.FromArgb((int)Math.Min(215, 68f * bb), pal[i]), 6f + 9f * bb))
                {
                    gp.StartCap = gp.EndCap = round;
                    g.DrawArc(gp, -r, -r, r * 2, r * 2, a0, sweep);
                }
            }
        }
        if (charge > 0.03f || warp > 0.03f)
        {
            float f = Math.Max(charge, warp * 0.8f);
            using (var flash = new Pen(Color.FromArgb((int)(185 * f), 255, 255, 255), 5f + 36f * f))
                g.DrawEllipse(flash, -r, -r, r * 2f, r * 2f);
            using (var core = new Pen(Color.FromArgb((int)(120 * f), 205, 232, 255), 2f + 12f * f))
                g.DrawEllipse(core, -r, -r, r * 2f, r * 2f);
        }
    }

    // The inner detail ring - 5 shorter arcs at r-13, drawn in its own
    // counter-rotating frame (see OnPaint). Colours use a stride of 5
    // (coprime with the 6-colour palette) so all five arcs are a
    // different hue - the old `i*3+1` stride only ever hit indices 1 and
    // 4, which is why it read as just orange + blue.
    void DrawInnerRing(Graphics g)
    {
        var pal = Vortex.RingColors;
        bool h = _hover || (Vortex.Fx.ReactiveRing && Vortex.Fx.PortalPull > 0.2f);
        var round = System.Drawing.Drawing2D.LineCap.Round;
        float r2 = 116f - 13f;
        for (int i = 0; i < 5; i++)
        {
            float a0 = i * 72f + 20f, sweep = 46f;
            Color c = pal[(i * 5) % pal.Length];
            using (var gp = new Pen(Color.FromArgb(h ? 72 : 50, c), 9f))
            {
                gp.StartCap = gp.EndCap = round;
                g.DrawArc(gp, -r2, -r2, r2 * 2, r2 * 2, a0, sweep);
            }
            using (var cp = new Pen(Color.FromArgb(h ? 255 : 215, c), 2.4f))
            {
                cp.StartCap = cp.EndCap = round;
                g.DrawArc(cp, -r2, -r2, r2 * 2, r2 * 2, a0, sweep);
            }
        }
    }
}

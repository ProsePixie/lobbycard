using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

class OpenVlcHandler
{
    // Watched-detection thresholds (plan item 16 / SPEC 5 - folded in from
    // the road's OpenVlcTracked.ps1 wrapper, which this supersedes; that
    // script + enable/disable-vlc-tracking.ps1 + the registry swap +
    // watched-auto.jsonl + merge-watched-log.ps1 are retired). No dependency
    // on VLC's flaky HTTP interface - same wall-clock approach that actually
    // worked on the road: launch, note the time, wait for VLC to close,
    // elapsed vs. duration decides it.
    const double WATCHED_FRACTION = 0.80;
    const int MIN_MINUTES_NO_DURATION = 40;
    const int MAX_TRACK_HOURS = 8;

    static void Main(string[] args)
    {
        string logFile = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "protocol_handler.log");
        try
        {
            if (args.Length < 1) return;
            string uri = args[0];
            File.AppendAllText(logFile, "[" + DateTime.Now.ToString("u") + "] openvlc.exe called with: " + uri + Environment.NewLine);

            // "|" joins multiple files that should play as one continuous
            // sequence (a DVD title split across several ~1GB VOB parts -
            // see pipeline\scan_media.py's process_video_ts_folder() and
            // app\render_engine.py's playlist_uri()). Illegal in a Windows
            // filename, so it can never collide with real path content.
            // Decode the WHOLE uri ONCE before splitting, not per-part after
            // splitting - a browser actually navigating to this link (as
            // opposed to one hand-typed/pasted straight into Run) percent-
            // encodes "|" into "%7C" as part of normal URL canonicalization.
            // Splitting on a literal "|" first (the original approach) never
            // finds that separator, so the whole multi-part playlist
            // collapsed into one bogus path with literal "|" characters
            // baked into it - not a real filename, so VLC just failed to
            // open it. Confirmed real via protocol_handler.log (2026-08-21):
            // both real DVD launches silently broke this way. Decoding first
            // makes a literal "|" and an already-encoded "%7C" converge to
            // the same thing before the split ever runs, so both a hand-
            // typed URI and a real browser-navigated one work identically.
            string raw = uri;
            int idx = raw.IndexOf(':');
            if (idx >= 0) raw = raw.Substring(idx + 1).TrimStart('/');
            string decoded = Uri.UnescapeDataString(raw);
            string[] paths = Array.ConvertAll(decoded.Split('|'), p => p.Replace('/', '\\'));
            string path = paths[0];
            File.AppendAllText(logFile, "  resolved path: " + path +
                (paths.Length > 1 ? " (+" + (paths.Length - 1) + " more part(s) queued)" : "") + Environment.NewLine);

            // Language detection only needs to run once - every part of the
            // same disc title shares the same audio/subtitle tracks.
            string langArgs = BuildLanguageArgs(path, logFile);

            string vlcPath = @"C:\Program Files\VideoLAN\VLC\vlc.exe";
            if (!File.Exists(vlcPath)) vlcPath = @"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe";
            if (File.Exists(vlcPath))
            {
                string quotedPaths = string.Join(" ", Array.ConvertAll(paths, p => "\"" + p + "\""));
                double durationSec = GetDurationSeconds(path, logFile);
                // Snapshot existing vlc.exe PIDs before launch - VLC
                // sometimes hands a new file off to an already-running
                // instance instead of actually staying alive as the process
                // we just started (same reason the old .ps1 had to diff
                // PIDs rather than trust Start-Process's own handle).
                int[] beforePids = SnapshotVlcPids();
                Process.Start(vlcPath, "--fullscreen " + langArgs + quotedPaths);
                DateTime startTime = DateTime.UtcNow;
                File.AppendAllText(logFile, "  vlc.exe launched OK (fullscreen" + (langArgs.Length > 0 ? ", " + langArgs.Trim() : "") +
                    (paths.Length > 1 ? ", " + paths.Length + "-part playlist" : "") +
                    (durationSec > 0 ? string.Format(", duration={0:F0}s", durationSec) : "") + ")" + Environment.NewLine);

                // Blocks this process until VLC closes (or MAX_TRACK_HOURS
                // elapses) - harmless, the OS already dispatched this .exe
                // as its own detached process; nothing is waiting on it.
                // --play-and-exit is deliberately NEVER passed anywhere in
                // this handler - on this VLC build it makes VLC quit
                // instantly on launch, so wall-clock is genuinely the only
                // reliable signal available.
                TrackWatched(path, durationSec, startTime, beforePids, logFile);
            }
            else
            {
                Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
                File.AppendAllText(logFile, "  vlc.exe not found, launched default handler instead" + Environment.NewLine);
            }
        }
        catch (Exception ex)
        {
            try { File.AppendAllText(logFile, "  ERROR: " + ex.Message + Environment.NewLine); } catch { }
        }
    }

    // ---- watched-tracking (plan item 16 / SPEC 5) --------------------------

    static void TrackWatched(string path, double durationSec, DateTime startTime, int[] beforePids, string logFile)
    {
        try
        {
            // Give VLC a few seconds to actually spawn/register its window
            // before diffing PIDs, same as the old .ps1.
            Thread.Sleep(5000);
            int[] ours = SnapshotVlcPids().Where(id => Array.IndexOf(beforePids, id) < 0).ToArray();
            if (ours.Length == 0) ours = SnapshotVlcPids(); // fallback: track any vlc instance

            DateTime deadline = startTime.AddHours(MAX_TRACK_HOURS);
            while (true)
            {
                Thread.Sleep(20000);
                bool anyStillRunning = ours.Any(IsPidRunning);
                if (!anyStillRunning) break;
                if (DateTime.UtcNow > deadline)
                {
                    File.AppendAllText(logFile, "  watch-tracking: max track time hit, stopping" + Environment.NewLine);
                    break;
                }
            }
            double elapsedSec = (DateTime.UtcNow - startTime).TotalSeconds;

            bool watched;
            string signal;
            if (durationSec >= 60)
            {
                double frac = elapsedSec / durationSec;
                watched = frac >= WATCHED_FRACTION;
                signal = string.Format("walltime:{0:F2}xdur", frac);
            }
            else
            {
                watched = elapsedSec >= (MIN_MINUTES_NO_DURATION * 60);
                signal = string.Format("walltime:{0:F0}min-nodur", elapsedSec / 60.0);
            }
            File.AppendAllText(logFile, string.Format(
                "  watch-tracking: elapsed={0:F0}s duration={1:F0}s watched={2} ({3})" + Environment.NewLine,
                elapsedSec, durationSec, watched, signal));
            if (!watched) return;

            RecordWatched(path, signal, logFile);
        }
        catch (Exception ex)
        {
            try { File.AppendAllText(logFile, "  watch-tracking failed: " + ex.Message + Environment.NewLine); } catch { }
        }
    }

    static int[] SnapshotVlcPids()
    {
        return Array.ConvertAll(Process.GetProcessesByName("vlc"), p => p.Id);
    }

    static bool IsPidRunning(int pid)
    {
        try { Process.GetProcessById(pid); return true; }
        catch (ArgumentException) { return false; }
    }

    // Path C: POST straight to the running server so the UI checkbox
    // updates live, no import round-trip needed. Static/offline fallback
    // (no server reachable, e.g. a travel drive with no bundled server
    // running - plan item 19): append a JSONL sidecar under travel-state\
    // (only if that folder already exists on this drive, i.e. a real
    // travel export - build_travel_plan.py always creates it) for
    // import_travel_changes.py to fold in on the next sync.
    // The project folder is the nearest parent of this exe that holds run.py (the
    // handlers normally live in extras\windows\handlers). A travel drive has no
    // run.py, so fall back to the exe's own parent folder, the original layout.
    static string FindProjectRoot()
    {
        string dir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
        for (string d = dir; !string.IsNullOrEmpty(d); d = Path.GetDirectoryName(d))
        {
            if (File.Exists(Path.Combine(d, "run.py"))) return d;
        }
        return Directory.GetParent(dir).FullName;
    }

    static void RecordWatched(string path, string signal, string logFile)
    {
        string projectRoot = FindProjectRoot();

        if (TryPostWatched(projectRoot, path, logFile)) return;

        string travelStateDir = Path.Combine(projectRoot, "travel-state");
        if (!Directory.Exists(travelStateDir))
        {
            File.AppendAllText(logFile, "  watch-tracking: server unreachable and no travel-state\\ on this drive - watched mark lost" + Environment.NewLine);
            return;
        }
        try
        {
            string rel = path;
            string rootPrefix = projectRoot.TrimEnd('\\') + "\\";
            if (path.StartsWith(rootPrefix, StringComparison.OrdinalIgnoreCase)) rel = path.Substring(rootPrefix.Length);
            rel = rel.Replace('\\', '/');
            string sidecar = Path.Combine(travelStateDir, "watched-auto.jsonl");
            string rec = "{\"rel_path\":\"" + JsonEscape(rel) + "\",\"abs_path\":\"" + JsonEscape(path) +
                "\",\"watched\":true,\"at\":\"" + DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ") +
                "\",\"signal\":\"" + JsonEscape(signal) + "\",\"source\":\"vlc-auto\"}";
            File.AppendAllText(sidecar, rec + Environment.NewLine);
            File.AppendAllText(logFile, "  watch-tracking: recorded to " + sidecar + Environment.NewLine);
        }
        catch (Exception ex)
        {
            File.AppendAllText(logFile, "  watch-tracking: sidecar write failed: " + ex.Message + Environment.NewLine);
        }
    }

    // Reads the per-session token web_server.py writes to data\session_auth.json
    // on every startup (regenerates each restart, same as the browser page's
    // own copy embedded via render_page()'s auth_token) - this .exe is a
    // separate OS process with no other way to learn it. Same trust
    // boundary as every other data file: local user account only.
    static bool TryPostWatched(string projectRoot, string path, string logFile)
    {
        try
        {
            string tokenPath = Path.Combine(projectRoot, "data", "session_auth.json");
            if (!File.Exists(tokenPath)) return false;
            string json = File.ReadAllText(tokenPath);
            string token = ExtractJsonField(json, "token");
            string portStr = ExtractJsonField(json, "port");
            if (string.IsNullOrEmpty(token)) return false;
            int port = 5183;
            if (!string.IsNullOrEmpty(portStr)) int.TryParse(portStr, out port);

            string url = "http://127.0.0.1:" + port + "/api/watched";
            var req = (HttpWebRequest)WebRequest.Create(url);
            req.Method = "POST";
            req.ContentType = "application/json";
            req.Headers["X-Auth-Token"] = token;
            req.Timeout = 5000;
            string body = "{\"path\":\"" + JsonEscape(path) + "\",\"watched\":true}";
            byte[] bytes = Encoding.UTF8.GetBytes(body);
            req.ContentLength = bytes.Length;
            using (var stream = req.GetRequestStream()) stream.Write(bytes, 0, bytes.Length);
            using (var resp = (HttpWebResponse)req.GetResponse())
            {
                int status = (int)resp.StatusCode;
                File.AppendAllText(logFile, "  watch-tracking: POST /api/watched -> " + status + Environment.NewLine);
                return status >= 200 && status < 300;
            }
        }
        catch (Exception ex)
        {
            File.AppendAllText(logFile, "  watch-tracking: POST /api/watched failed (" + ex.Message + ") - server likely not running" + Environment.NewLine);
            return false;
        }
    }

    static string ExtractJsonField(string json, string key)
    {
        var m = Regex.Match(json, "\"" + Regex.Escape(key) + "\"\\s*:\\s*\"?([^\",}]+?)\"?\\s*[,}]");
        return m.Success ? m.Groups[1].Value : null;
    }

    static string JsonEscape(string s)
    {
        return s.Replace("\\", "\\\\").Replace("\"", "\\\"");
    }

    static double GetDurationSeconds(string videoPath, string logFile)
    {
        try
        {
            string ffprobe = FindFfprobe();
            if (ffprobe == null) return 0.0;
            var psi = new ProcessStartInfo(
                ffprobe,
                "-v error -show_entries format=duration -of default=nw=1:nk=1 \"" + videoPath + "\"")
            {
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using (var proc = Process.Start(psi))
            {
                string output = proc.StandardOutput.ReadToEnd().Trim();
                proc.WaitForExit(8000);
                double d;
                if (double.TryParse(output, System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out d))
                    return d;
            }
        }
        catch (Exception ex)
        {
            File.AppendAllText(logFile, "  duration probe failed: " + ex.Message + Environment.NewLine);
        }
        return 0.0;
    }

    // Conditional audio/subtitle auto-selection (Feature_Requests.md item -
    // VLC conditional audio/subtitle auto-selection). Runs ffprobe directly
    // here, in real time, rather than reading anything precomputed by the
    // Python pipeline (pipeline\probe_runtime.py runs during the nightly
    // scan, a completely separate process with no connection to this
    // protocol handler at the moment a user clicks Play) - genuinely the
    // simpler, more correct design once traced through, not a shortcut:
    // always fresh, works even for files the pipeline hasn't scanned yet,
    // no schema/URI-encoding plumbing needed to carry the flag across.
    //
    // Decided behavior: English audio present -> force it, subtitles off
    // (a same-named external .srt sidecar would otherwise still get
    // auto-loaded, so --no-sub-autodetect-file is needed alongside --no-spu,
    // not just one or the other). No English audio tag -> leave audio
    // alone (don't guess which foreign track to force), prefer an English
    // subtitle track if the file actually has one. Never guesses beyond
    // that: any ffprobe failure (missing exe, corrupt file, timeout) or a
    // genuinely untagged file (confirmed real case: an older .avi release
    // with zero language tags on any stream) falls through to "" - VLC's
    // own default behavior, unchanged from before this feature existed.
    static string BuildLanguageArgs(string videoPath, string logFile)
    {
        try
        {
            string ffprobe = FindFfprobe();
            if (ffprobe == null)
            {
                File.AppendAllText(logFile, "  ffprobe not found, using VLC defaults" + Environment.NewLine);
                return "";
            }

            string audioLangs = RunFfprobe(ffprobe, videoPath, "a");
            if (ContainsEnglishTag(audioLangs))
            {
                File.AppendAllText(logFile, "  English audio tag found - forcing English audio, subtitles off" + Environment.NewLine);
                return "--audio-language=eng --no-spu --no-sub-autodetect-file ";
            }

            string subLangs = RunFfprobe(ffprobe, videoPath, "s");
            bool hasSubs = !string.IsNullOrWhiteSpace(subLangs);
            File.AppendAllText(logFile, "  no English audio tag" + (hasSubs ? " - preferring English subtitles if available" : " - no subtitle tracks either, using VLC defaults") + Environment.NewLine);
            return hasSubs ? "--sub-language=eng " : "";
        }
        catch (Exception ex)
        {
            File.AppendAllText(logFile, "  language detection failed (" + ex.Message + "), using VLC defaults" + Environment.NewLine);
            return "";
        }
    }

    static string FindFfprobe()
    {
        // Same PATH-first-then-hardcoded-fallback precedent as
        // pipeline\probe_runtime.py's _ffprobe_path().
        try
        {
            var psi = new ProcessStartInfo("where", "ffprobe")
            {
                RedirectStandardOutput = true,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            using (var proc = Process.Start(psi))
            {
                string output = proc.StandardOutput.ReadToEnd().Trim();
                proc.WaitForExit(3000);
                if (proc.ExitCode == 0 && output.Length > 0)
                    return output.Split('\n')[0].Trim();
            }
        }
        catch { }
        string fallback = @"C:\ffmpeg\ffmpeg\bin\ffprobe.exe";
        return File.Exists(fallback) ? fallback : null;
    }

    static string RunFfprobe(string ffprobe, string videoPath, string streamType)
    {
        var psi = new ProcessStartInfo(
            ffprobe,
            "-v error -select_streams " + streamType + " -show_entries stream_tags=language -of default=noprint_wrappers=1:nokey=1 \"" + videoPath + "\"")
        {
            RedirectStandardOutput = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        using (var proc = Process.Start(psi))
        {
            string output = proc.StandardOutput.ReadToEnd();
            proc.WaitForExit(8000);
            return output;
        }
    }

    static bool ContainsEnglishTag(string ffprobeOutput)
    {
        if (string.IsNullOrWhiteSpace(ffprobeOutput)) return false;
        foreach (var line in ffprobeOutput.Split('\n'))
        {
            string t = line.Trim().ToLowerInvariant();
            if (t == "eng" || t == "en") return true;
        }
        return false;
    }
}

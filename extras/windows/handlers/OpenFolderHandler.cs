using System;
using System.Diagnostics;
using System.IO;

class OpenFolderHandler
{
    static void Main(string[] args)
    {
        string logFile = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "protocol_handler.log");
        try
        {
            if (args.Length < 1) return;
            string uri = args[0];
            File.AppendAllText(logFile, "[" + DateTime.Now.ToString("u") + "] openfolder.exe called with: " + uri + Environment.NewLine);

            string path = uri;
            int idx = path.IndexOf(':');
            if (idx >= 0) path = path.Substring(idx + 1).TrimStart('/');
            path = Uri.UnescapeDataString(path);
            path = path.Replace('/', '\\');
            File.AppendAllText(logFile, "  resolved path: " + path + Environment.NewLine);

            if (File.Exists(path))
            {
                // A loose video file (scanned directly, not inside its own
                // release folder) has this same path for both its "entry"
                // and its actual video file - explorer.exe "<file>" with no
                // /select flag launches the file itself via its default
                // handler instead of showing it in its folder, which is what
                // "Open folder" actually means. /select opens the containing
                // folder with the file highlighted, same as Explorer's own
                // "Show in folder" - never launches it.
                Process.Start("explorer.exe", "/select,\"" + path + "\"");
                File.AppendAllText(logFile, "  path is a file - opened containing folder with it selected" + Environment.NewLine);
            }
            else
            {
                Process.Start("explorer.exe", "\"" + path + "\"");
                File.AppendAllText(logFile, "  explorer.exe launched OK" + Environment.NewLine);
            }
        }
        catch (Exception ex)
        {
            try { File.AppendAllText(logFile, "  ERROR: " + ex.Message + Environment.NewLine); } catch { }
        }
    }
}

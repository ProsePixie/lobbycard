# Windows protocol handlers (optional)

The app can play a file and show it in the file manager on its own, on every OS: the Play button starts
VLC on the machine running the server, and the folder icon opens its file manager. You do **not** need
these handlers for that.

They exist for the one case that needs a real OS hook: a *browser on another Windows PC* clicking an
`openvlc://` or `openfolder://` link. Each link is handed to a tiny compiled launcher registered for
your user account (no admin rights, nothing system-wide):

| Handler | What it does |
| --- | --- |
| `OpenFolderHandler.cs` | opens the folder in Explorer, with the file selected when the link points at one |
| `OpenVlcHandler.cs` | plays the file(s) in VLC fullscreen, picks English audio / English subtitles for foreign films (via `ffprobe`), plays multi-part DVD titles back to back, and marks the title watched near the end by telling the running server |

They are compiled executables, not scripts, on purpose: a browser that launches a hidden PowerShell with a
bypassed execution policy matches the signature of real protocol-hijack malware, and security suites
block it.

```powershell
powershell -ExecutionPolicy Bypass -File extras\windows\handlers\build_handlers.ps1
```

To remove: delete `HKCU:\Software\Classes\openfolder` and `HKCU:\Software\Classes\openvlc`.
The built `.exe` files are git-ignored; they are always built from the `.cs` files here.

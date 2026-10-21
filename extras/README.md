# Extras

Optional pieces that are not part of the core. Nothing here is needed to run `python run.py`.

| Folder | What | Platform |
| --- | --- | --- |
| [`youtube/`](youtube/README.md) | a curated YouTube channel block that plugs into the page | any |
| [`vortex/`](vortex/README.md) | a launch-pad window (C#, WinForms) | Windows |
| [`windows/handlers/`](windows/handlers/README.md) | `openvlc://` and `openfolder://` protocol handlers, for clicking those links from another Windows PC | Windows |

The core also contains Windows-only behaviour (popup window placement), which switches itself off on other systems.

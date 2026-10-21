# Demo script

A five-minute walkthrough for showing this project to someone: an interviewer, a collaborator, a friend.
Everything runs on the invented demo library, so nothing personal is on screen.

## Before you start (one minute)

```bash
python run.py --demo --rebuild         # fresh sample library
```

Open <http://127.0.0.1:5183> in a window about 1300 px wide, dark theme. Have these ready in other tabs or
windows:

- the setup page: run `python run.py --port 5190` in another terminal with `MEDIABOX_CONFIG` and `MEDIABOX_DATA_DIR`
  pointing at an empty folder (the demo library is under `demo_data/library`, which is a good folder to point it at);
- the terminal, at the repo root, for `python -m pytest`;
- the phone remote: <http://127.0.0.1:5184> in a landscape window, or the screenshot in `docs/screenshots/remote.png`.

If you only have a minute or no machine, play `docs/demo.mp4` (40 seconds, same tour) or open the Pages build.

## The tour

| Time | Show | Say |
| --- | --- | --- |
| 0:00 | The home page | "It is a catalog for video files you already own. Posters and metadata come from TMDB, watched history is yours. All three panels are computed on the server and the page is one self-contained document, no build step." |
| 0:30 | Type `tea`, then `lighthouse` in the search box | "Everything here is client-side filtering over data embedded in the page." |
| 1:00 | Open *The Salt Lighthouse*, scroll the episodes, tick one | "Watched marks are saved on the server. I'll prove that in a second." |
| 1:30 | Reload the page; the tick is still there. Optionally `cat demo_data/user_state.json` | "State lives in one JSON file, written atomically, through a single lock. Several things write to it: the page, the VLC watcher thread, the filing step." |
| 2:00 | Franchise mode (the play icon in the header) | "Optional mode driven by a pack file: a saga in story order with its own palette and a *what's next* panel. In my own copy it is a film universe; in the repo it is an invented one." |
| 2:30 | The setup page: type the demo library path, press *Look inside* | "First-run setup: pick folders, test the TMDB key live, and a dry run that reads file names only. It tells you what it found and what each sample file would be recognised as before anything is saved." |
| 3:15 | Terminal: `python -m pytest -q` | "About 240 tests. The ones I'm proudest of: the whole pipeline runs against a fake TMDB server, a real browser clicks through every screen and fails on any JavaScript error, and every state-changing route is enumerated from the URL map and has to refuse a request without the token." |
| 4:00 | `docs/screenshots/remote.png` or the phone | "A phone on the same network gets a landscape remote for the VLC that's playing on the TV. It is its own port so a bookmark can never land on the full library." |
| 4:30 | `README.md`, the *Engineering notes* | Pick one story (below). |

## Stories to tell (pick one or two)

- **Picking the right TMDB title.** String similarity alone prefers an obscure namesake over the show everyone
  means. Year bonus, a capped popularity nudge, and a near-tie report so a human is asked instead of guessing.
  `pipeline/enrich_tmdb.py: best_match`, `tests/test_matching.py`.
- **Bugs the tests found.** Writing the browser and pipeline tests turned up three real bugs: a file name containing
  `</script>` could break out of the page's data block; every TV show was re-fetched from TMDB on every refresh
  because one cache field was never saved; and the scripts crashed on a fresh clone because the data folder did not
  exist.
- **The monolith that became files.** The UI started as a 9,000-line Python string. It is now three real files plus
  fourteen JavaScript files, and I verified the split by rendering before and after and comparing the output
  byte for byte.
- **Atomic writes.** Temp file and `os.replace`, a shared retry for the Windows case where another process holds a
  read handle at that exact moment, and a lock around the one file that cannot be rebuilt.
- **The YouTube premiere bug.** Premieres look like Shorts while they are airing; burying a video needs two
  independent signals and anything that was ever live is deferred, not buried.
- **DPI-aware window placement.** Python is DPI-unaware, so on a scaled display it and Chrome disagree about pixels.

## Questions you may get

**Why Flask and a hand-rolled page instead of a framework?** It started as a personal tool that needed to run with
one `pip install` and no build step. The cost is real: the front end is one global scope, not modules. The page is
already split into files, so moving to modules is a refactor with a safety net (the browser tests), not a rewrite.

**How does it scale?** The page embeds the whole library, about 600 bytes per file. Measured on an invented
2,400-title, 28,000-file library: a 41 MB page, rendered in about a second, first grid under a second from local
disk. It is fine on a LAN and it is the first thing to change (lazy-loading per-title details) for a much larger
library.

**Is it safe to run?** It listens on localhost by default, every mutating route needs a per-run token and a
same-origin request, the `Host` header is checked against DNS rebinding, and nothing is ever deleted: removing a
watched file moves it to a `Pending Deletion` folder. `SECURITY.md` has the model and `tests/test_security.py` the checks.

**What did you not do?** Be honest about it: macOS and Linux are covered by CI but I have only run it by hand on
Windows. The Windows launcher and protocol handlers are optional extras. The original build's portable "travel"
export is not included. The front end is not modular. The scanner is tuned to common release-name shapes and
everything stranger goes through a data file of per-library fixes.

**What is original and what is a service?** TMDB provides metadata under your own API key; the repo ships none of
it. The demo library, the artwork and the loading-screen text are original and generated.

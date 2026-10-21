# Changelog

## 0.1.0 (first public version)

Built from a private, long-running personal project. What is new in this version is everything that makes it
usable by someone else.

- **Settings page** (Admin menu → Settings): change the media folders and TMDB key at any time, and change the title, subtitle, header image (with image position), accent colour and watch-list names with a live preview. Appearance applies on the next page load.
- **Setup page** for a first run: folder pickers, a live TMDB key test, a read-only dry run, then save and restart.
- **Packs**: name, tagline, art, loading-screen text, household names, accent colour and an optional franchise
  mode all come from a folder, not from code. The built-in pack is original, generated artwork.
- **Demo mode** (`python run.py --demo`) with an invented library, and a **static, read-only build** of it for
  GitHub Pages (`tools/build_static_demo.py`).
- **Cross-platform core.** Windows-only behaviour is gated and the Windows extras (VORTEX launcher, protocol
  handlers) live in `extras/`.
- **The page is real files** (`app/templates`, `app/static`) rather than a 9,000-line string; the rendered output
  is byte-identical to what the string produced.
- **Tests**: unit, security, an end-to-end pipeline run against a fake TMDB server, and real-browser tests. CI on
  Windows, macOS and Linux, a secret scan, and a guard against personal paths and branded art.
- **Fixes found while writing the tests**: a title containing `</script>` could break out of the page's data block;
  every TV show was re-fetched from TMDB on every refresh because a cache marker was never saved; scripts crashed on
  a fresh clone because the data folder did not exist; and a missing import for the Amazon storefront setting.
- **Hardening**: a `Host`-header check against DNS rebinding, a token on the library-data endpoint, and the
  unused folder-picker endpoint removed.

### Removed

- The portable "Travel Mode" export (it depended on a Windows build step that is not part of this release).
- Per-library data that used to live in code (specific shows' episode fixes, curated lists) now lives in
  `scan_overrides.json` and `library_rules.json`.

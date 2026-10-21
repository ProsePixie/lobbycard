"""
Builds a static, read-only copy of the demo library that any web host can serve
(GitHub Pages, for instance). No server, no Python and no network is needed to
browse it.

    python tools/build_static_demo.py            # writes site/index.html and site/images/
    python tools/build_static_demo.py --out dist

Everything in it is the invented demo library. Actions that need the server
(marking watched, filing, playing) are shown disabled, exactly as the static
snapshot from app/generate_html.py shows them.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

NOTICE = (
    '<div style="margin:0 0 14px;padding:10px 14px;border:1px solid var(--border);border-radius:10px;'
    'background:var(--accent-soft);color:var(--text);font-size:14px;line-height:1.4">'
    '<strong>Read-only demo.</strong> Every title here is invented and every poster is generated. '
    'The real app runs locally on your own library and can mark things watched, file downloads and play on your TV: '
    'run it with <code>python run.py --demo</code>.</div>'
)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(ROOT, "site"), help="output folder (default: site/)")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as data:
        env = dict(os.environ, MEDIABOX_DEMO="1", MEDIABOX_DATA_DIR=data)
        for k in [k for k in env if k.startswith("MEDIABOX_") and k not in ("MEDIABOX_DEMO", "MEDIABOX_DATA_DIR")]:
            env.pop(k)
        subprocess.run([sys.executable, os.path.join(ROOT, "demo", "make_demo.py")], check=True, env=env, cwd=ROOT,
                       stdout=subprocess.DEVNULL)

        # Render in a child process so the demo configuration (pack, data folder) is applied cleanly.
        notice_file = os.path.join(data, "notice.html")
        with open(notice_file, "w", encoding="utf-8") as f:
            f.write(NOTICE)
        html = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "_render_static.py"), data, notice_file],
                              check=True, env=env, cwd=ROOT, capture_output=True).stdout

        out = os.path.abspath(args.out)
        if os.path.isdir(out):
            shutil.rmtree(out)
        os.makedirs(out)
        with open(os.path.join(out, "index.html"), "wb") as f:
            f.write(html)
        for kind in ("poster", "backdrop", "rec_poster"):
            src = os.path.join(data, "tmdb_images", kind)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(out, "images", kind))
        open(os.path.join(out, ".nojekyll"), "w").close()
    n = sum(len(files) for _, _, files in os.walk(os.path.join(out, "images")))
    print(f"Wrote {out}  (index.html {len(html) // 1024} KB, {n} images)")


if __name__ == "__main__":
    main()

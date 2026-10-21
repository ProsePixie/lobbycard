"""
A small fake TMDB, enough for the pipeline to run against with no network and no key.

    srv = FakeTMDB()           # starts on a free port in a background thread
    env["MEDIABOX_TMDB_API_BASE"] = srv.api_base
    env["MEDIABOX_TMDB_IMAGE_BASE"] = srv.image_base
    env["MEDIABOX_TMDB_API_KEY"] = "0" * 32

It knows a handful of invented titles (see CATALOG). Every request is recorded in
`srv.requests` so a test can assert what the pipeline asked for (and what it did not).
"""
import json
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# id -> (kind, name, first date, status, seasons: [episode count per season])
CATALOG = {
    1001: ("tv", "Brightwater Academy", "2021-03-01", "Returning Series", [5, 4]),
    1002: ("tv", "Night Ferry", "2024-05-01", "Ended", [3]),
    2001: ("movie", "The Film", "2019-06-01", "Released", []),
    # a remake pair: same title, different decade, to exercise the year bonus
    2002: ("movie", "Second Summit", "1974-01-01", "Released", []),
    2003: ("movie", "Second Summit", "2019-01-01", "Released", []),
}
GENRES = {"tv": [{"id": 18, "name": "Drama"}], "movie": [{"id": 18, "name": "Drama"}, {"id": 35, "name": "Comedy"}]}


def _png():
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"\x00" + b"\x20\x40\x80" * 2
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG = _png()


def _result(tid):
    kind, name, date, status, seasons = CATALOG[tid]
    base = {"id": tid, "popularity": 20.0, "overview": f"About {name}.", "poster_path": f"/p{tid}.png", "backdrop_path": f"/b{tid}.png",
            "vote_average": 7.4, "genre_ids": [18]}
    if kind == "tv":
        base.update(name=name, first_air_date=date)
    else:
        base.update(title=name, release_date=date)
    return base


def _detail(tid):
    kind, name, date, status, seasons = CATALOG[tid]
    d = _result(tid)
    d["genres"] = GENRES[kind][:1]
    d["credits"] = {"cast": [{"id": 9000 + tid, "name": "Mara Lindqvist", "character": "Lead", "profile_path": None, "order": 0}], "crew": []}
    d["videos"] = {"results": []}
    d["recommendations"] = {"results": [_result(t) for t in CATALOG if t != tid and CATALOG[t][0] == kind][:2]}
    if kind == "tv":
        d.update(status=status, type="Scripted", number_of_seasons=len(seasons), episode_run_time=[42], created_by=[{"name": "Elin Marsh"}],
                 external_ids={"imdb_id": None}, aggregate_credits=d["credits"],
                 next_episode_to_air=None, seasons=[{"season_number": i + 1, "episode_count": n} for i, n in enumerate(seasons)])
    else:
        d.update(runtime=105, imdb_id=None, status=status)
    return d


def _season(tid, n):
    kind, name, date, status, seasons = CATALOG[tid]
    year = int(date[:4]) + n - 1
    eps = [{"episode_number": e, "season_number": n, "name": f"Episode {e}", "air_date": f"{year}-04-{e:02d}", "runtime": 42,
            "overview": "", "id": tid * 100 + n * 10 + e} for e in range(1, seasons[n - 1] + 1)]
    return {"id": tid * 10 + n, "season_number": n, "episodes": eps, "name": f"Season {n}"}


class FakeTMDB:
    def __init__(self):
        self.requests = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                u = urlparse(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                outer.requests.append(u.path)
                if u.path.startswith("/t/p/"):
                    return self._send(200, PNG, "image/png")
                if not u.path.startswith("/3/"):
                    return self._send(404, {"status_message": "not found"})
                if len(q.get("api_key", "")) < 8:
                    return self._send(401, {"status_message": "Invalid API key"})
                p = u.path[2:]
                if p == "/configuration":
                    return self._send(200, {"images": {}})
                if p.startswith("/genre/"):
                    return self._send(200, {"genres": GENRES[p.split("/")[2]]})
                if p.startswith("/search/"):
                    kind, query = p.split("/")[2], q.get("query", "").lower()
                    hits = [_result(t) for t, c in CATALOG.items() if c[0] == kind and query and (query in c[1].lower() or c[1].lower() in query)]
                    return self._send(200, {"page": 1, "results": hits})
                parts = p.strip("/").split("/")
                if parts[0] in ("tv", "movie") and parts[1].isdigit() and int(parts[1]) in CATALOG:
                    tid = int(parts[1])
                    if len(parts) == 2:
                        return self._send(200, _detail(tid))
                    if len(parts) == 4 and parts[2] == "season" and CATALOG[tid][0] == "tv" and 1 <= int(parts[3]) <= len(CATALOG[tid][4]):
                        return self._send(200, _season(tid, int(parts[3])))
                return self._send(404, {"status_message": "The resource you requested could not be found."})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def api_base(self):
        return f"http://127.0.0.1:{self.port}/3"

    @property
    def image_base(self):
        return f"http://127.0.0.1:{self.port}/t/p"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

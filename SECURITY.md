# Security

## Model

This is a local application that can move and rename files, so it is built defensively. The short version
(the full one is in [ARCHITECTURE.md](ARCHITECTURE.md#security-model)):

- it listens on `127.0.0.1` unless you pass `--lan`;
- every route that changes anything needs a random per-run token, and same-origin requests only;
- requests whose `Host` header is not `localhost`, an IP address, or (with `--lan`) a `.local` or machine name are
  refused, which defeats DNS rebinding;
- the setup page only answers requests from the same machine;
- your TMDB key is stored in a file with owner-only permissions and never sent to the page or logged;
- nothing is deleted: "delete" moves files to a `Pending Deletion` folder.

Running with `--lan` shows your library to everyone on that network (the page itself is not password protected;
mutations still need the token the page embeds). Do not expose the port to the internet.

## Reporting a problem

Please open a private security advisory on the repository, or contact the maintainer directly, rather than filing
a public issue. Include the version (`pyproject.toml`), your OS, and steps to reproduce. You can expect an
acknowledgement within a week.

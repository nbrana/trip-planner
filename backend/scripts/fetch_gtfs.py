"""Download the NJ Transit bus GTFS feed using developer-portal credentials.

1. Register at https://developer.njtransit.com/ and request Bus API access.
2. Put NJT_USERNAME / NJT_PASSWORD in backend/.env
3. uv run python scripts/fetch_gtfs.py

The API flow (authenticateUser -> UserToken -> getGTFS) uses NJ Transit's
GTFSG2 API, which serves the bus feed (BUSDV2 is the bus departures API and has
no getGTFS endpoint). If NJ Transit changes endpoint names,
check https://pcsdata.njtransit.com/swagger and adjust NJT_BUS_API_BASE. Alternatively, download the bus GTFS zip manually from the portal and
save it as backend/data/njt_bus_gtfs.zip.
"""

import argparse
import sys
import zipfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import DEFAULT_GTFS, get_settings  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_GTFS)
    args = ap.parse_args()

    s = get_settings()
    if not (s.njt_username and s.njt_password):
        sys.exit("Set NJT_USERNAME and NJT_PASSWORD in backend/.env (see .env.example).")

    with httpx.Client(timeout=300, headers={"User-Agent": s.user_agent}) as c:
        r = c.post(f"{s.njt_bus_api_base}/authenticateUser",
                   files={"username": (None, s.njt_username), "password": (None, s.njt_password)})
        r.raise_for_status()
        body = r.json()
        token = body.get("UserToken")
        if not token or str(body.get("Authenticated", "")).lower() != "true":
            sys.exit(f"Authentication failed: {body}")
        print("Authenticated; downloading bus GTFS...")
        r = c.post(f"{s.njt_bus_api_base}/getGTFS", files={"token": (None, token)})
        r.raise_for_status()

    if not r.content.startswith(b"PK"):
        sys.exit(f"Expected a zip file but got: {r.text[:300]}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(".tmp")
    tmp.write_bytes(r.content)
    with zipfile.ZipFile(tmp) as z:
        names = {Path(n).name for n in z.namelist()}
    missing = {"stops.txt", "routes.txt", "trips.txt", "stop_times.txt"} - names
    if missing:
        tmp.unlink()
        sys.exit(f"Downloaded zip is missing {sorted(missing)}")
    tmp.replace(args.out)
    print(f"Saved {args.out} ({len(r.content) / 1e6:.1f} MB). Restart the backend to load it.")


if __name__ == "__main__":
    main()

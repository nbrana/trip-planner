"""Download the NJ Transit bus GTFS feed using developer-portal credentials.

1. Register at https://developer.njtransit.com/ and request bus data access.
2. Put NJT_USERNAME / NJT_PASSWORD in backend/.env
3. uv run python scripts/fetch_gtfs.py

The running backend also does this automatically once a day (GTFS_REFRESH_HOURS) and
reloads the schedule when the file changes, so this script is mainly for first setup.

The API flow (authenticateUser -> UserToken -> getGTFS) uses NJ Transit's GTFSG2 API,
which serves the bus feed. If NJ Transit changes endpoint names, check
https://pcsdata.njtransit.com/swagger and adjust NJT_BUS_API_BASE. Alternatively,
download the bus GTFS zip manually and save it as backend/data/njt_bus_gtfs.zip.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import DEFAULT_GTFS, get_settings  # noqa: E402
from app.njt import NJTClient  # noqa: E402


async def run(out: Path) -> None:
    client = NJTClient(get_settings())
    try:
        print("Downloading bus GTFS...")
        changed = await client.download_gtfs(out)
    finally:
        await client.close()
    size = out.stat().st_size / 1e6
    print(f"Saved {out} ({size:.1f} MB)." if changed else f"{out} is already up to date ({size:.1f} MB).")
    if changed:
        print("A running backend reloads it automatically within a few minutes.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_GTFS)
    args = ap.parse_args()
    try:
        asyncio.run(run(args.out))
    except (ValueError, RuntimeError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()

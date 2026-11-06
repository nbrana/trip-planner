"""Overpass API helper: public instances are often overloaded (429/502/504), so try mirrors with backoff."""

import sys
import time

import httpx

MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def query(q: str, user_agent: str, primary: str | None = None, attempts: int = 3, wait_s: float = 30) -> dict:
    urls = [primary] if primary else []
    urls += [m for m in MIRRORS if m != primary]
    errors: list[str] = []
    with httpx.Client(timeout=600, headers={"User-Agent": user_agent}) as c:
        for attempt in range(attempts):
            for url in urls:
                try:
                    r = c.post(url, data={"data": q})
                    r.raise_for_status()
                    data = r.json()  # overloaded servers sometimes return an HTML error page with 200
                    if "remark" in data and "error" in data["remark"].lower():
                        raise ValueError(data["remark"][:200])
                    return data
                except (httpx.HTTPError, ValueError) as e:
                    msg = f"{url}: {e.response.status_code if isinstance(e, httpx.HTTPStatusError) else e}"
                    print(f"  {msg}", file=sys.stderr, flush=True)
                    errors.append(msg)
            if attempt + 1 < attempts:
                print(f"  all servers failed; retrying in {wait_s:.0f}s...", file=sys.stderr, flush=True)
                time.sleep(wait_s)
    raise RuntimeError("Overpass query failed on every server:\n  " + "\n  ".join(errors))

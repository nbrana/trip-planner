"""NJ Transit developer API (GTFSG2): shared token handling, static GTFS download, GTFS-RT feeds."""

from __future__ import annotations

import json
import logging
import zipfile
from collections.abc import Callable
from pathlib import Path

import httpx
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2 as pb

from .config import Settings

log = logging.getLogger(__name__)
REQUIRED_GTFS = {"stops.txt", "routes.txt", "trips.txt", "stop_times.txt"}


class NJTClient:
    def __init__(self, settings: Settings):
        if not (settings.njt_username and settings.njt_password):
            raise ValueError("NJT_USERNAME and NJT_PASSWORD are required")
        self.s = settings
        self.base = settings.njt_bus_api_base.rstrip("/")
        self._token: str | None = None
        self._token_file = settings.cache_dir / "njt_token.json"
        self.http = httpx.AsyncClient(timeout=300, headers={"User-Agent": settings.user_agent})

    async def close(self) -> None:
        await self.http.aclose()

    # NJT limits authentication calls, so the token is reused and cached on disk.
    def _load_token(self) -> str | None:
        try:
            data = json.loads(self._token_file.read_text())
            return data["token"] if data.get("base") == self.base else None
        except (OSError, ValueError, KeyError):
            return None

    async def _authenticate(self) -> str:
        r = await self.http.post(f"{self.base}/authenticateUser",
                                 files={"username": (None, self.s.njt_username), "password": (None, self.s.njt_password)})
        r.raise_for_status()
        body = r.json()
        token = body.get("UserToken")
        if not token or str(body.get("Authenticated", "")).lower() != "true":
            raise RuntimeError(f"NJ Transit authentication failed: {body.get('errorMessage') or body}")
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        self._token_file.write_text(json.dumps({"base": self.base, "token": token}))
        log.info("Authenticated with NJ Transit API")
        return token

    async def call(self, endpoint: str, valid: Callable[[bytes], bool]) -> bytes:
        """POST an endpoint with the token; re-authenticate once if the response isn't valid."""
        r = None
        for attempt in range(2):
            if self._token is None:
                self._token = (self._load_token() if attempt == 0 else None) or await self._authenticate()
            r = await self.http.post(f"{self.base}/{endpoint}", files={"token": (None, self._token)})
            if r.is_success and valid(r.content):
                return r.content
            log.info("%s rejected (HTTP %s); re-authenticating", endpoint, r.status_code)
            self._token = None
        raise RuntimeError(f"{endpoint}: no valid response (HTTP {r.status_code}): {r.text[:200]}")

    async def feed(self, endpoint: str) -> pb.FeedMessage:
        msg = pb.FeedMessage()

        def valid(content: bytes) -> bool:
            if content.lstrip()[:1] in (b"{", b"<", b"["):  # JSON/HTML error, not protobuf
                return False
            try:
                msg.ParseFromString(content)
                return True
            except DecodeError:
                return False

        await self.call(endpoint, valid)
        return msg

    async def download_gtfs(self, out: Path) -> bool:
        """Download the bus GTFS zip to `out` atomically. Returns False if unchanged."""
        content = await self.call("getGTFS", lambda c: c.startswith(b"PK"))
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_bytes(content)
        try:
            with zipfile.ZipFile(tmp) as z:
                missing = REQUIRED_GTFS - {Path(n).name for n in z.namelist()}
        except zipfile.BadZipFile as e:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"Downloaded GTFS is not a valid zip: {e}") from e
        if missing:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"Downloaded GTFS is missing {sorted(missing)}")
        if out.exists() and out.read_bytes() == content:
            tmp.unlink()
            return False
        tmp.replace(out)
        return True

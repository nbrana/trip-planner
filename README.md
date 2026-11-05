# NJ Bus Trip Planner

This planner covers NJ Transit buses. It uses the static GTFS schedule for the buses and OpenStreetMap for maps, geocoding, park & ride lots and optional street routing.

- **Walk access**: set a max walking distance. It applies to the first stop, transfers, and the destination.
- **Park & Ride access**: set a max driving distance. The planner drives you to a nearby P&R lot (from OSM), adds parking time, then walks you to a stop near the lot.
- The planner returns the best options for each access mode, with several departures, colored route legs, and stop-by-stop times.
- **Real-time**: with NJ Transit credentials, the backend checks NJT's live feeds every 30 seconds. Routing uses predicted times and skips canceled trips. The app shows on-time/late badges, live bus positions on the map, and route alerts.

```
backend/   FastAPI + pandas/numpy, RAPTOR transit router (Python)
frontend/  React + TypeScript + Tailwind v4 + Leaflet (Vite)
```

## Quick start (no API tokens needed)

The repo ships with a **synthetic sample feed** (fake routes S1–S3 near Montclair/Bloomfield) so everything runs before you have NJ Transit credentials.

```bash
# backend (http://localhost:8000)
cd backend
uv sync
uv run uvicorn app.main:app --reload
uv run pytest                                # planner tests
# (data/sample/ can be regenerated with: uv run python scripts/make_sample_gtfs.py)

# frontend (http://localhost:5173, proxies /api to :8000)
cd frontend
npm install
npm run dev
```

## Loading real data

1. **NJ Transit bus GTFS.** Register at <https://developer.njtransit.com/> and request bus data access. Copy `backend/.env.example` to `backend/.env` and set `NJT_USERNAME` / `NJT_PASSWORD`, then run:
   ```bash
   uv run python scripts/fetch_gtfs.py        # -> data/njt_bus_gtfs.zip
   ```
   You can also download the bus GTFS zip from the portal yourself and save it as `backend/data/njt_bus_gtfs.zip`.
2. **Park & ride lots** (from OpenStreetMap, no key needed):
   ```bash
   uv run python scripts/fetch_park_rides.py  # -> data/park_rides.json
   ```
3. **Set `USER_AGENT`** in `.env` to something that identifies you. Nominatim's usage policy requires this.
4. **Restart the backend.** The first load parses the feed (this can take a minute for the full NJT bus feed). After that, a pickle cache in `data/cache/` makes startup fast.

The backend uses those files automatically when they exist. You can override the paths with `GTFS_PATH` / `PARK_RIDE_PATH`.

## Real-time

Real-time turns on automatically when `NJT_USERNAME` / `NJT_PASSWORD` are set and the real feed is loaded (never with sample data). It uses the same `GTFSG2` API as the schedule download:

- **Trip updates**: per-stop predicted delays are applied to the schedule. A delay carries forward to later stops that have no prediction. Canceled trips are removed. The service day is worked out from each update's scheduled time, because NJT leaves `start_date` empty.
- **Vehicle positions**: attached to bus legs and refreshed in the UI every 20 seconds.
- **Alerts**: route-level alerts only, refreshed every 5 minutes. NJT's stop-only alerts are station signage greetings, so they're dropped.

The API token is cached in `data/cache/njt_token.json` so restarts don't re-authenticate, and it is renewed automatically if NJT rejects it. `/api/health` reports feed status and errors. Set `REALTIME_ENABLED=false` to turn it off.

## How routing works

- **Transit**: [RAPTOR](https://www.microsoft.com/en-us/research/publication/round-based-public-transit-routing/). Trips are grouped into stop patterns. Each round adds one more bus ride, and the router returns results that are best on arrival time versus number of transfers. After-midnight trips from the previous service day are included.
- **Walk and drive legs** are estimated from straight-line distance × a detour factor ÷ speed (all configurable in `.env`). Set `USE_OSRM=true` to use the public OSRM servers instead. Drive times then come from real roads, and walk and drive legs follow the actual streets on the map.
- **Arrive by**: the same RAPTOR runs on a time-reversed view of the timetable (stop order reversed, times negated). That finds the *latest* departure that still arrives by the deadline, and the result is converted back to forward order. Live delays apply the same way.
- **Multiple options**: the search re-runs just after each found departure (or, for arrive-by, with a deadline just before each found arrival) to get more options. Options another option matches or beats on leave time, arrival and transfers are dropped.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Feed stats, whether sample data is in use |
| POST | `/api/plan` | Plan a trip (see `PlanRequest` in `app/schemas.py`) |
| GET | `/api/geocode?q=` | Address search (Nominatim, NJ-biased) |
| GET | `/api/reverse?lat=&lon=` | Reverse geocode |
| GET | `/api/park-rides?lat=&lon=&radius_m=` | P&R lots near a point |
| GET | `/api/vehicles?trip_id=&route_id=` | Live vehicle positions |
| GET | `/api/alerts?route_id=` | Active route alerts |

Interactive docs: <http://localhost:8000/docs>.

## Known limitations / next steps

- Public Nominatim/OSRM/OSM tiles are for light use only. Self-host them for anything beyond development.
- Map data © OpenStreetMap contributors (ODbL).

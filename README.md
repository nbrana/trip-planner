# NJ Bus Trip Planner

This planner covers NJ Transit buses. It uses the static GTFS schedule for the buses and OpenStreetMap for maps, geocoding, park & ride lots and optional street routing.

- **Walk access**: set a max walking distance. It applies to the first stop, transfers, and the destination.
- **Park & Ride access**: set a max driving distance. The planner drives you to a nearby P&R lot (from OSM), adds parking time, then walks you to a stop near the lot.
- The planner returns the best options for each access mode, with several departures, colored route legs, and stop-by-stop times.

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

## How routing works

- **Transit**: [RAPTOR](https://www.microsoft.com/en-us/research/publication/round-based-public-transit-routing/). Trips are grouped into stop patterns. Each round adds one more bus ride, and the router returns results that are best on arrival time versus number of transfers. After-midnight trips from the previous service day are included.
- **Walk and drive legs** are estimated from straight-line distance × a detour factor ÷ speed (all configurable in `.env`). Set `USE_OSRM=true` to use the public OSRM servers instead. Drive times then come from real roads, and walk and drive legs follow the actual streets on the map.
- **Multiple options**: the search re-runs just after each found departure to get later options.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Feed stats, whether sample data is in use |
| POST | `/api/plan` | Plan a trip (see `PlanRequest` in `app/schemas.py`) |
| GET | `/api/geocode?q=` | Address search (Nominatim, NJ-biased) |
| GET | `/api/reverse?lat=&lon=` | Reverse geocode |
| GET | `/api/park-rides?lat=&lon=&radius_m=` | P&R lots near a point |

Interactive docs: <http://localhost:8000/docs>.

## Known limitations / next steps

- Real-time data is not used yet. NJT's GTFS-RT feeds use the same developer credentials.
- Each search uses a single departure time. There is no "arrive by" option yet.
- Public Nominatim/OSRM/OSM tiles are for light use only. Self-host them for anything beyond development.
- Map data © OpenStreetMap contributors (ODbL).

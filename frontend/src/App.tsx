import { useEffect, useState } from "react";
import ItineraryList from "./components/ItineraryList";
import LocationSearch from "./components/LocationSearch";
import MapView from "./components/MapView";
import PlanOptions, { type Options } from "./components/PlanOptions";
import { BusIcon, SwapIcon } from "./components/Icons";
import { api } from "./lib/api";
import { miToM } from "./lib/format";
import type { Health, Lot, Place, PlanResponse } from "./lib/types";

const DEFAULT_OPTIONS: Options = {
  departAt: "",
  walkAccess: true,
  parkRide: false,
  maxWalkMi: 0.5,
  maxDriveMi: 5,
  maxTransfers: 2,
};
const OPTIONS_KEY = "trip-planner-options";

function loadOptions(): Options {
  try {
    const saved = JSON.parse(localStorage.getItem(OPTIONS_KEY) ?? "{}");
    return { ...DEFAULT_OPTIONS, ...saved, departAt: "" };
  } catch {
    return DEFAULT_OPTIONS;
  }
}

type Target = "origin" | "destination";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [origin, setOrigin] = useState<Place | null>(null);
  const [destination, setDestination] = useState<Place | null>(null);
  const [options, setOptions] = useState<Options>(loadOptions);
  const [picking, setPicking] = useState<Target | null>(null);
  const [lots, setLots] = useState<Lot[]>([]);
  const [result, setResult] = useState<PlanResponse | null>(null);
  const [selected, setSelected] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setBackendDown(true));
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(OPTIONS_KEY, JSON.stringify(options));
    } catch {
      /* storage unavailable */
    }
  }, [options]);

  useEffect(() => {
    if (!origin || !options.parkRide) {
      setLots([]);
      return;
    }
    const t = setTimeout(() => {
      api.lots(origin.lat, origin.lon, miToM(options.maxDriveMi)).then(setLots).catch(() => setLots([]));
    }, 250);
    return () => clearTimeout(t);
  }, [origin, options.parkRide, options.maxDriveMi]);

  // Results go stale when inputs change.
  useEffect(() => {
    setResult(null);
  }, [origin, destination]);

  const setTarget = (t: Target, p: Place | null) => (t === "origin" ? setOrigin(p) : setDestination(p));

  const onMapClick = picking
    ? (lat: number, lon: number) => {
        const target = picking;
        const label = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
        setTarget(target, { name: label, lat, lon });
        setPicking(null);
        api
          .reverse(lat, lon)
          .then((r) => r && setTarget(target, { name: r.name, lat, lon }))
          .catch(() => {});
      }
    : null;

  const canPlan = origin && destination && (options.walkAccess || options.parkRide) && !loading;

  const plan = async () => {
    if (!origin || !destination) return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.plan({
        origin: { lat: origin.lat, lon: origin.lon, name: origin.name },
        destination: { lat: destination.lat, lon: destination.lon, name: destination.name },
        depart_at: options.departAt || null,
        walk_access: options.walkAccess,
        park_ride: options.parkRide,
        max_walk_m: miToM(options.maxWalkMi),
        max_drive_m: miToM(options.maxDriveMi),
        max_transfers: options.maxTransfers,
        num_itineraries: 3,
      });
      setResult(res);
      setSelected(0);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const itinerary = result?.itineraries[selected] ?? null;

  return (
    <div className={`flex h-full flex-col-reverse bg-slate-50 text-slate-900 md:flex-row ${picking ? "picking" : ""}`}>
      <aside className="flex h-[55%] w-full flex-col border-slate-200 bg-white md:h-full md:w-[400px] md:border-r">
        <header className="flex items-center gap-2 border-b border-slate-200 px-4 py-3">
          <div className="rounded-lg bg-blue-600 p-1.5 text-white"><BusIcon className="h-5 w-5" /></div>
          <div>
            <h1 className="text-base font-semibold leading-tight">NJ Bus Trip Planner</h1>
            <p className="text-xs text-slate-500">NJ Transit buses + OpenStreetMap</p>
          </div>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {backendDown && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              Can't reach the backend. Start it with <code>uv run uvicorn app.main:app --reload</code> in <code>backend/</code>.
            </div>
          )}
          {health?.using_sample_data && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
              Using <strong>synthetic sample data</strong> (routes S1–S3 near Montclair/Bloomfield). Add NJ Transit
              credentials and run <code>scripts/fetch_gtfs.py</code> to load real bus schedules.
            </div>
          )}

          <div className="relative space-y-3">
            <LocationSearch label="From" dotClass="bg-green-600" value={origin} onChange={setOrigin}
              picking={picking === "origin"} onPick={() => setPicking(picking === "origin" ? null : "origin")} />
            <LocationSearch label="To" dotClass="bg-red-600" value={destination} onChange={setDestination}
              picking={picking === "destination"} onPick={() => setPicking(picking === "destination" ? null : "destination")} />
            <button
              onClick={() => { setOrigin(destination); setDestination(origin); }}
              title="Swap"
              className="absolute right-11 top-[46px] z-10 rounded-full border border-slate-300 bg-white p-1 text-slate-500 shadow-sm hover:text-slate-800"
            >
              <SwapIcon className="h-3.5 w-3.5" />
            </button>
            {picking && (
              <p className="text-xs text-blue-700">Click the map to set the {picking === "origin" ? "starting point" : "destination"}.</p>
            )}
          </div>

          <PlanOptions value={options} onChange={setOptions} />

          <button
            onClick={plan}
            disabled={!canPlan}
            className="w-full rounded-xl bg-blue-600 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "Planning…" : "Plan trip"}
          </button>
          {!options.walkAccess && !options.parkRide && (
            <p className="text-xs text-red-600">Choose walking and/or Park & Ride.</p>
          )}
          {error && <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}

          {result && (
            <section className="space-y-2">
              {result.notes.map((n, i) => (
                <p key={i} className="rounded-lg bg-slate-100 p-2.5 text-xs text-slate-600">{n}</p>
              ))}
              {result.itineraries.length > 0 && (
                <ItineraryList itineraries={result.itineraries} selected={selected} onSelect={setSelected} />
              )}
            </section>
          )}
        </div>
      </aside>

      <main className="relative h-[45%] flex-1 md:h-full">
        <MapView
          origin={origin}
          destination={destination}
          walkRadiusM={miToM(options.maxWalkMi)}
          driveRadiusM={options.parkRide ? miToM(options.maxDriveMi) : null}
          lots={lots}
          itinerary={itinerary}
          onMapClick={onMapClick}
        />
      </main>
    </div>
  );
}

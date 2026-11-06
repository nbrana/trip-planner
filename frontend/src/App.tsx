import { useEffect, useMemo, useState } from "react";
import ItineraryList from "./components/ItineraryList";
import LocationSearch from "./components/LocationSearch";
import MapView from "./components/MapView";
import PlanOptions, { type Options } from "./components/PlanOptions";
import { BusIcon, SwapIcon } from "./components/Icons";
import { api } from "./lib/api";
import { formatTime, miToM } from "./lib/format";
import { addRecent, clearRecents, loadRecents, removeRecent } from "./lib/recents";
import type { Health, Lot, Place, PlanResponse, RouteInfo, Vehicle } from "./lib/types";

const DEFAULT_OPTIONS: Options = {
  timeMode: "depart",
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
    // Times aren't restored (they go stale); arrive-by without a time falls back to leaving now.
    return { ...DEFAULT_OPTIONS, ...saved, departAt: "", timeMode: "depart" };
  } catch {
    return DEFAULT_OPTIONS;
  }
}

type Target = "origin" | "destination";

const VEHICLE_POLL_MS = 20000;
const HEALTH_POLL_MS = 30000;

/** Warn when the static schedule is about to run out (or has). */
function ScheduleBanner({ health }: { health: Health | null }) {
  if (!health?.feed_end || health.using_sample_data) return null;
  const end = new Date(`${health.feed_end}T23:59:59`);
  const days = Math.floor((end.getTime() - Date.now()) / 86400e3);
  if (days > 2) return null;
  const endText = end.toLocaleDateString([], { month: "short", day: "numeric" });
  const fix = health.schedule.auto_download
    ? health.schedule.error
      ? `Automatic update failed: ${health.schedule.error}`
      : "It updates automatically; check back soon."
    : "Run scripts/fetch_gtfs.py to download a new one.";
  return (
    <div className={`rounded-lg border p-3 text-xs ${days < 0 ? "border-red-200 bg-red-50 text-red-700" : "border-amber-200 bg-amber-50 text-amber-800"}`}>
      {days < 0 ? <>Bus schedule data <strong>expired on {endText}</strong>. </> : <>Bus schedule data ends <strong>{endText}</strong>. </>}
      {fix}
    </div>
  );
}

function LiveStatus({ health }: { health: Health | null }) {
  const rt = health?.realtime;
  if (!rt) return null;
  if (!rt.enabled)
    return <span className="text-xs text-slate-400" title="Add NJ Transit credentials for live data">Schedule only</span>;
  const ok = !rt.error && rt.last_update && !rt.stale;
  const title = rt.error
    ? `Live data error: ${rt.error}`
    : `${rt.vehicles} buses tracked · ${rt.trips_with_predictions} trips with predictions · ${rt.alerts} alerts`;
  return (
    <span className={`flex items-center gap-1.5 text-xs ${ok ? "text-emerald-700" : "text-amber-700"}`} title={title}>
      <span className={`h-2 w-2 rounded-full ${ok ? "bg-emerald-500" : "bg-amber-500"}`} />
      {ok ? `Live · ${formatTime(rt.last_update!)}` : rt.last_update ? "Live data delayed" : "Connecting…"}
    </span>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [origin, setOrigin] = useState<Place | null>(null);
  const [destination, setDestination] = useState<Place | null>(null);
  const [options, setOptions] = useState<Options>(loadOptions);
  const [picking, setPicking] = useState<Target | null>(null);
  const [lots, setLots] = useState<Lot[]>([]);
  const [result, setResult] = useState<(PlanResponse & { arriveBy: boolean }) | null>(null);
  const [selected, setSelected] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [vehicles, setVehicles] = useState<Record<string, Vehicle>>({});
  const [plannedAt, setPlannedAt] = useState<Date | null>(null);
  const [recents, setRecents] = useState<Place[]>(loadRecents);
  const remember = (...ps: (Place | null)[]) =>
    setRecents((list) => ps.reduce((l, p) => (p ? addRecent(l, p) : l), list));

  useEffect(() => {
    const load = () =>
      api
        .health()
        .then((h) => {
          setHealth(h);
          setBackendDown(false);
        })
        .catch(() => setBackendDown(true));
    load();
    const t = setInterval(load, HEALTH_POLL_MS);
    return () => clearInterval(t);
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

  const arriveBy = options.timeMode === "arrive";
  const canPlan =
    origin && destination && (options.walkAccess || options.parkRide) && (!arriveBy || options.departAt) && !loading;

  const plan = async (keepSelection = false) => {
    if (!origin || !destination) return;
    remember(destination, origin); // origin ends up most recent
    setLoading(true);
    setError(null);
    try {
      const res = await api.plan({
        origin: { lat: origin.lat, lon: origin.lon, name: origin.name },
        destination: { lat: destination.lat, lon: destination.lon, name: destination.name },
        depart_at: arriveBy ? null : options.departAt || null,
        arrive_by: arriveBy ? options.departAt : null,
        walk_access: options.walkAccess,
        park_ride: options.parkRide,
        max_walk_m: miToM(options.maxWalkMi),
        max_drive_m: miToM(options.maxDriveMi),
        max_transfers: options.maxTransfers,
        num_itineraries: 3,
      });
      setResult({ ...res, arriveBy });
      setPlannedAt(new Date());
      setSelected((s) => (keepSelection && s < res.itineraries.length ? s : 0));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const itinerary = result?.itineraries[selected] ?? null;

  // Poll live positions of the buses in the selected itinerary.
  const busLegs = useMemo(() => itinerary?.legs.filter((l) => l.mode === "bus" && l.trip_id) ?? [], [itinerary]);
  const tripKey = busLegs.map((l) => l.trip_id).join(",");
  const realtimeOn = !!health?.realtime.enabled;
  useEffect(() => {
    setVehicles({});
    if (!realtimeOn || !tripKey) return;
    const ids = tripKey.split(",");
    const load = () =>
      api
        .vehicles(ids)
        .then((vs) => setVehicles(Object.fromEntries(vs.map((v) => [v.trip_id, v]))))
        .catch(() => {});
    load();
    const t = setInterval(load, VEHICLE_POLL_MS);
    return () => clearInterval(t);
  }, [tripKey, realtimeOn]);

  const liveBuses = busLegs.flatMap((l) => {
    const v = vehicles[l.trip_id!] ?? l.vehicle;
    return v ? [{ vehicle: v, route: l.route as RouteInfo }] : [];
  });

  return (
    <div className={`flex h-full flex-col-reverse bg-slate-50 text-slate-900 md:flex-row ${picking ? "picking" : ""}`}>
      <aside className="flex h-[55%] w-full flex-col border-slate-200 bg-white md:h-full md:w-[400px] md:border-r">
        <header className="flex items-center gap-2 border-b border-slate-200 px-4 py-3">
          <div className="rounded-lg bg-blue-600 p-1.5 text-white"><BusIcon className="h-5 w-5" /></div>
          <div>
            <h1 className="text-base font-semibold leading-tight">NJ Bus Trip Planner</h1>
            <p className="text-xs text-slate-500">NJ Transit buses + OpenStreetMap</p>
          </div>
          <div className="ml-auto">
            <LiveStatus health={health} />
          </div>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {backendDown && (
            <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
              Can't reach the backend. Start it with <code>uv run uvicorn app.main:app --reload</code> in <code>backend/</code>.
            </div>
          )}
          <ScheduleBanner health={health} />
          {health?.using_sample_data && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
              Using <strong>synthetic sample data</strong> (routes S1–S3 near Montclair/Bloomfield). Add NJ Transit
              credentials and run <code>scripts/fetch_gtfs.py</code> to load real bus schedules.
            </div>
          )}

          <div className="relative space-y-3">
            <LocationSearch label="From" dotClass="bg-green-600" value={origin} onChange={(p) => { setOrigin(p); remember(p); }}
              recents={recents} onRemoveRecent={(p) => setRecents((l) => removeRecent(l, p))}
              onClearRecents={() => setRecents(clearRecents())}
              picking={picking === "origin"} onPick={() => setPicking(picking === "origin" ? null : "origin")} />
            <LocationSearch label="To" dotClass="bg-red-600" value={destination} onChange={(p) => { setDestination(p); remember(p); }}
              recents={recents} onRemoveRecent={(p) => setRecents((l) => removeRecent(l, p))}
              onClearRecents={() => setRecents(clearRecents())}
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
            onClick={() => plan()}
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
                <>
                  <div className="flex items-center justify-between text-xs text-slate-500">
                    <span>
                      {result.itineraries.some((i) => i.legs.some((l) => l.realtime)) ? "Times include live predictions" : "Scheduled times"}
                      {plannedAt && ` · as of ${plannedAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`}
                    </span>
                    <button
                      onClick={() => plan(true)}
                      disabled={loading}
                      className="rounded-md px-2 py-1 font-medium text-blue-700 hover:bg-blue-50 disabled:opacity-50"
                    >
                      ↻ Refresh
                    </button>
                  </div>
                  <ItineraryList itineraries={result.itineraries} selected={selected} onSelect={setSelected}
                    vehicles={vehicles} arriveBy={result.arriveBy} />
                </>
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
          liveBuses={liveBuses}
        />
      </main>
    </div>
  );
}

import { Fragment } from "react";
import { formatAgo, formatDelay, formatDistance, formatDuration, formatTime, shortName } from "../lib/format";
import type { Itinerary, Leg, Vehicle } from "../lib/types";
import { BusIcon, CarIcon, WalkIcon } from "./Icons";

const MODE_LABEL = { walk: "Walk", park_ride: "Park & Ride", walk_only: "Walk only" } as const;

function RouteBadge({ leg }: { leg: Leg }) {
  const r = leg.route!;
  return (
    <span className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-semibold"
      style={{ background: r.color, color: r.text_color }}>
      <BusIcon className="h-3.5 w-3.5" />
      {r.short_name || r.long_name}
    </span>
  );
}

const TONE = { ok: "text-emerald-700", late: "text-red-600", early: "text-amber-600" } as const;

function LiveBadge({ leg }: { leg: Leg }) {
  if (!leg.realtime || leg.delay_s == null) return null;
  const d = formatDelay(leg.delay_s);
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${TONE[d.tone]}`} title="Live prediction">
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-60" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-current" />
      </span>
      {d.text}
    </span>
  );
}

/** Live time, with the scheduled time struck through when they differ by a minute or more. */
function LiveTime({ time, scheduled }: { time: string; scheduled: string | null }) {
  const off = scheduled ? Math.abs(Date.parse(time) - Date.parse(scheduled)) >= 60000 : false;
  return (
    <span className="tabular-nums">
      <span className="font-medium">{formatTime(time)}</span>
      {off && <span className="ml-1 text-xs text-slate-400 line-through">{formatTime(scheduled!)}</span>}
    </span>
  );
}

function AlertList({ leg }: { leg: Leg }) {
  if (!leg.alerts.length) return null;
  return (
    <div className="mt-1 space-y-1">
      {leg.alerts.map((a) => (
        <details key={a.id} className="rounded-md bg-amber-50 px-2 py-1 text-xs text-amber-900">
          <summary className="cursor-pointer select-none font-medium">⚠ {a.header || "Service alert"}</summary>
          {a.description && a.description !== a.header && <p className="mt-1 whitespace-pre-line">{a.description}</p>}
          {a.url && <a href={a.url} target="_blank" rel="noreferrer" className="underline">More info</a>}
        </details>
      ))}
    </div>
  );
}

function LegChip({ leg }: { leg: Leg }) {
  if (leg.mode === "bus") return <RouteBadge leg={leg} />;
  const Icon = leg.mode === "drive" ? CarIcon : WalkIcon;
  const mins = Math.round((Date.parse(leg.end) - Date.parse(leg.start)) / 60000);
  return (
    <span className="inline-flex items-center gap-0.5 text-xs text-slate-600">
      <Icon className="h-4 w-4" />
      {mins > 0 && <span className="tabular-nums">{mins}</span>}
    </span>
  );
}

function LegDetail({ leg, vehicle }: { leg: Leg; vehicle: Vehicle | null }) {
  const dur = formatDuration((Date.parse(leg.end) - Date.parse(leg.start)) / 1000);
  if (leg.mode === "bus") {
    const r = leg.route!;
    return (
      <div className="border-l-4 py-1 pl-3" style={{ borderColor: r.color }}>
        <div className="flex items-center gap-2">
          <RouteBadge leg={leg} />
          <span className="min-w-0 flex-1 truncate text-sm text-slate-700">{leg.headsign || r.long_name}</span>
          <LiveBadge leg={leg} />
        </div>
        {vehicle && (
          <p className="mt-0.5 text-xs text-slate-500">
            Bus {vehicle.label ?? ""}{vehicle.occupancy ? ` · ${vehicle.occupancy}` : ""}
            {vehicle.timestamp ? ` · seen ${formatAgo(vehicle.timestamp)}` : ""}
          </p>
        )}
        <AlertList leg={leg} />
        <p className="mt-1 text-sm">
          <LiveTime time={leg.start} scheduled={leg.scheduled_start} /> Board at {leg.from_place.name}
        </p>
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer select-none py-0.5">
            {leg.intermediate_stops.length + 1} stops · {dur}
          </summary>
          <ul className="ml-2 space-y-0.5">
            {leg.intermediate_stops.map((s, i) => (
              <li key={i}><span className="tabular-nums">{formatTime(s.time)}</span> {s.name}</li>
            ))}
          </ul>
        </details>
        <p className="text-sm">
          <LiveTime time={leg.end} scheduled={leg.scheduled_end} /> Get off at {leg.to_place.name}
        </p>
      </div>
    );
  }
  const Icon = leg.mode === "drive" ? CarIcon : WalkIcon;
  return (
    <div className={`border-l-4 border-dotted py-1 pl-3 ${leg.mode === "drive" ? "border-violet-500" : "border-slate-400"}`}>
      <p className="flex items-center gap-1.5 text-sm text-slate-700">
        <Icon className="h-4 w-4" />
        <span>
          {leg.mode === "drive" ? "Drive" : "Walk"} {formatDistance(leg.distance_m)} ({dur}) to{" "}
          <span className="font-medium">{shortName(leg.to_place.name)}</span>
        </span>
      </p>
      {leg.mode === "drive" && <p className="text-xs text-slate-500">Allow a few minutes to park.</p>}
    </div>
  );
}

interface Props {
  itineraries: Itinerary[];
  selected: number;
  onSelect: (i: number) => void;
  vehicles: Record<string, Vehicle>;
  arriveBy?: boolean;
}

export default function ItineraryList({ itineraries, selected, onSelect, vehicles, arriveBy = false }: Props) {
  return (
    <ul className="space-y-2">
      {itineraries.map((it, i) => {
        const active = i === selected;
        const firstBus = it.legs.find((l) => l.mode === "bus");
        const alertCount = it.legs.reduce((n, l) => n + l.alerts.length, 0);
        return (
          <li key={i}>
            <div
              role="button"
              tabIndex={0}
              onClick={() => onSelect(i)}
              onKeyDown={(e) => e.key === "Enter" && onSelect(i)}
              className={`cursor-pointer rounded-xl border p-3 transition ${
                active ? "border-blue-500 bg-white shadow-md ring-1 ring-blue-500" : "border-slate-200 bg-white hover:border-slate-300"
              }`}
            >
              <div className="flex items-baseline justify-between gap-2">
                {arriveBy ? (
                  <span className="text-base text-slate-900">
                    <span className="font-semibold">Leave by {formatTime(it.start)}</span>
                    <span className="text-sm tabular-nums text-slate-500"> · arrive {formatTime(it.end)}</span>
                  </span>
                ) : (
                  <span className="text-base font-semibold tabular-nums text-slate-900">
                    {formatTime(it.start)} – {formatTime(it.end)}
                  </span>
                )}
                <span className="text-sm font-medium text-slate-700">{formatDuration(it.duration_s)}</span>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-1">
                {it.legs.map((l, j) => (
                  <Fragment key={j}>
                    {j > 0 && <span className="text-slate-300">›</span>}
                    <LegChip leg={l} />
                  </Fragment>
                ))}
              </div>
              <div className="mt-1.5 flex flex-wrap gap-x-3 text-xs text-slate-500">
                <span className={it.access_mode === "park_ride" ? "font-medium text-violet-700" : ""}>
                  {MODE_LABEL[it.access_mode]}
                </span>
                {it.access_mode !== "walk_only" && (
                  <span>{it.transfers === 0 ? "No transfers" : `${it.transfers} transfer${it.transfers > 1 ? "s" : ""}`}</span>
                )}
                <span>{formatDistance(it.walk_m)} walk</span>
                {it.drive_m > 0 && <span>{formatDistance(it.drive_m)} drive</span>}
                {it.wait_s >= 60 && <span>{formatDuration(it.wait_s)} waiting</span>}
              </div>
              {(firstBus?.realtime || alertCount > 0) && (
                <div className="mt-1 flex items-center gap-3">
                  {firstBus && <LiveBadge leg={firstBus} />}
                  {alertCount > 0 && (
                    <span className="text-xs font-medium text-amber-700">
                      ⚠ {alertCount} alert{alertCount > 1 ? "s" : ""}
                    </span>
                  )}
                </div>
              )}
              {active && (
                <div className="mt-3 space-y-2 border-t border-slate-100 pt-3">
                  {it.park_ride_lot && (
                    <p className="text-xs text-violet-700">Park at <span className="font-medium">{it.park_ride_lot}</span></p>
                  )}
                  {it.legs.map((l, j) => (
                    <LegDetail key={j} leg={l} vehicle={(l.trip_id && vehicles[l.trip_id]) || l.vehicle} />
                  ))}
                  <p className="text-sm"><span className="font-medium tabular-nums">{formatTime(it.end)}</span> Arrive</p>
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

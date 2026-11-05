import type { Place } from "./types";

const KEY = "trip-planner-recent-places";
const MAX = 8;

// Two places closer than ~30 m are treated as the same place.
const samePlace = (a: Place, b: Place) =>
  Math.abs(a.lat - b.lat) < 0.0003 && Math.abs(a.lon - b.lon) < 0.0003;

/** Coordinate-only labels from map clicks before reverse geocoding finishes. */
const isCoordLabel = (name: string) => /^-?\d+\.\d+, -?\d+\.\d+$/.test(name);

export function loadRecents(): Place[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return Array.isArray(raw)
      ? raw.filter((p) => p && typeof p.name === "string" && Number.isFinite(p.lat) && Number.isFinite(p.lon)).slice(0, MAX)
      : [];
  } catch {
    return [];
  }
}

function save(list: Place[]): Place[] {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    /* storage unavailable: recents just won't persist */
  }
  return list;
}

/** Move (or add) a place to the front. A named entry isn't replaced by a bare-coordinates one. */
export function addRecent(list: Place[], p: Place): Place[] {
  const existing = list.find((q) => samePlace(q, p));
  const entry: Place = {
    name: existing && isCoordLabel(p.name) && !isCoordLabel(existing.name) ? existing.name : p.name,
    lat: p.lat,
    lon: p.lon,
  };
  return save([entry, ...list.filter((q) => !samePlace(q, p))].slice(0, MAX));
}

export function removeRecent(list: Place[], p: Place): Place[] {
  return save(list.filter((q) => !samePlace(q, p)));
}

export function clearRecents(): Place[] {
  return save([]);
}

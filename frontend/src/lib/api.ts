import type { Health, Lot, Place, PlanRequest, PlanResponse } from "./types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => fetch("/api/health").then((r) => json<Health>(r)),
  geocode: (q: string) => fetch(`/api/geocode?q=${encodeURIComponent(q)}`).then((r) => json<Place[]>(r)),
  reverse: (lat: number, lon: number) =>
    fetch(`/api/reverse?lat=${lat}&lon=${lon}`).then((r) => json<Place | null>(r)),
  lots: (lat: number, lon: number, radius_m: number) =>
    fetch(`/api/park-rides?lat=${lat}&lon=${lon}&radius_m=${radius_m}`).then((r) => json<Lot[]>(r)),
  plan: (body: PlanRequest) =>
    fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<PlanResponse>(r)),
};

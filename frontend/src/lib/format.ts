export const M_PER_MI = 1609.344;

export const miToM = (mi: number) => mi * M_PER_MI;

export function formatDistance(m: number): string {
  const mi = m / M_PER_MI;
  if (mi < 0.1) return `${Math.round(m * 3.28084)} ft`;
  return `${mi < 10 ? mi.toFixed(1) : Math.round(mi)} mi`;
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" });
}

export function formatDuration(s: number): string {
  const m = Math.max(1, Math.round(s / 60));
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} hr${m % 60 ? ` ${m % 60} min` : ""}`;
}

/** Value for <input type="datetime-local"> in the browser's local time. */
export function localInputValue(d = new Date()): string {
  const off = d.getTimezoneOffset() * 60000;
  return new Date(d.getTime() - off).toISOString().slice(0, 16);
}

export function shortName(name: string): string {
  return name.split(",").slice(0, 2).join(",");
}

export const LEG_COLORS = { walk: "#475569", drive: "#7c3aed" } as const;

/** "On time" / "3 min late" / "2 min early" from a delay in seconds. */
export function formatDelay(s: number): { text: string; tone: "ok" | "late" | "early" } {
  const m = Math.round(s / 60);
  if (m === 0) return { text: "On time", tone: "ok" };
  if (m > 0) return { text: `${m} min late`, tone: "late" };
  return { text: `${-m} min early`, tone: "early" };
}

export function formatAgo(epochS: number | null, now = Date.now()): string {
  if (!epochS) return "";
  const s = Math.max(0, Math.round(now / 1000 - epochS));
  return s < 60 ? `${s}s ago` : `${Math.round(s / 60)} min ago`;
}

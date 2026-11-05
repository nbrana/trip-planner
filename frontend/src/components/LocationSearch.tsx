import { useState } from "react";
import { api } from "../lib/api";
import { shortName } from "../lib/format";
import type { Place } from "../lib/types";
import { ClockIcon, PinIcon } from "./Icons";

interface Props {
  label: string;
  dotClass: string;
  value: Place | null;
  onChange: (p: Place | null) => void;
  picking: boolean;
  onPick: () => void;
  recents: Place[];
  onRemoveRecent: (p: Place) => void;
  onClearRecents: () => void;
}

// Nominatim's usage policy forbids search-as-you-type, so address search runs on Enter / button.
// Recent places are local, so they filter as you type.
export default function LocationSearch({
  label, dotClass, value, onChange, picking, onPick, recents, onRemoveRecent, onClearRecents,
}: Props) {
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState(false);
  const [focused, setFocused] = useState(false);
  const [results, setResults] = useState<Place[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const search = async () => {
    if (query.trim().length < 3) return;
    setLoading(true);
    setError(null);
    try {
      setResults(await api.geocode(query.trim()));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const choose = (p: Place) => {
    onChange(p);
    setResults(null);
    setEditing(false);
    setFocused(false);
    setQuery("");
  };

  const close = () => {
    setEditing(false);
    setResults(null);
    setFocused(false);
  };

  const showInput = editing || !value;
  const q = query.trim().toLowerCase();
  const matchingRecents = recents.filter((r) => !q || r.name.toLowerCase().includes(q));
  const showRecents = showInput && focused && !results && matchingRecents.length > 0;

  return (
    <div className="relative">
      <label className="mb-1 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-slate-500">
        <span className={`h-2.5 w-2.5 rounded-full ${dotClass}`} />
        {label}
      </label>
      <div className="flex gap-1.5">
        {showInput ? (
          <form
            className="flex min-w-0 flex-1"
            onSubmit={(e) => {
              e.preventDefault();
              search();
            }}
          >
            <input
              autoFocus={editing}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setResults(null);
              }}
              onFocus={() => setFocused(true)}
              // Delay so a click on a dropdown item lands before it closes.
              onBlur={() => setTimeout(() => setFocused(false), 150)}
              onKeyDown={(e) => e.key === "Escape" && close()}
              placeholder="Address or place, then Enter"
              aria-label={label}
              className="min-w-0 flex-1 rounded-l-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
            />
            <button
              type="submit"
              disabled={loading || query.trim().length < 3}
              className="rounded-r-lg border border-l-0 border-slate-300 bg-slate-50 px-3 text-sm text-slate-700 hover:bg-slate-100 disabled:opacity-50"
            >
              {loading ? "…" : "Search"}
            </button>
          </form>
        ) : (
          <button
            onClick={() => setEditing(true)}
            title={value.name}
            className="min-w-0 flex-1 truncate rounded-lg border border-slate-300 bg-white px-3 py-2 text-left text-sm hover:border-slate-400"
          >
            {shortName(value.name)}
          </button>
        )}
        <button
          onClick={onPick}
          title="Pick on map"
          className={`rounded-lg border px-2.5 ${
            picking ? "border-blue-500 bg-blue-50 text-blue-600" : "border-slate-300 text-slate-600 hover:bg-slate-50"
          }`}
        >
          <PinIcon />
        </button>
      </div>
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}

      {showRecents && (
        <div
          className="absolute z-[1000] mt-1 w-full overflow-hidden rounded-lg border border-slate-200 bg-white text-sm shadow-lg"
          onMouseDown={(e) => e.preventDefault() /* keep input focus while clicking */}
        >
          <div className="flex items-center justify-between px-3 pb-1 pt-2 text-xs font-medium uppercase tracking-wide text-slate-400">
            <span>Recent</span>
            <button onClick={onClearRecents} className="normal-case tracking-normal text-slate-500 hover:text-slate-800">
              Clear
            </button>
          </div>
          <ul className="max-h-64 overflow-auto pb-1">
            {matchingRecents.map((r) => (
              <li key={`${r.lat},${r.lon}`} className="group flex items-center hover:bg-slate-50">
                <button onClick={() => choose(r)} title={r.name} className="flex min-w-0 flex-1 items-center gap-2 px-3 py-2 text-left">
                  <ClockIcon className="h-4 w-4 shrink-0 text-slate-400" />
                  <span className="truncate">{shortName(r.name)}</span>
                </button>
                <button
                  onClick={() => onRemoveRecent(r)}
                  aria-label={`Remove ${shortName(r.name)} from recent places`}
                  className="px-3 py-2 text-slate-400 opacity-60 hover:text-slate-700 group-hover:opacity-100"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {results && (
        <ul className="absolute z-[1000] mt-1 max-h-64 w-full overflow-auto rounded-lg border border-slate-200 bg-white text-sm shadow-lg">
          {results.length === 0 && <li className="px-3 py-2 text-slate-500">No matches</li>}
          {results.map((r, i) => (
            <li key={i}>
              <button onClick={() => choose(r)} className="w-full px-3 py-2 text-left hover:bg-slate-50">
                {r.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

import { CarIcon, WalkIcon } from "./Icons";

export interface Options {
  departAt: string;
  walkAccess: boolean;
  parkRide: boolean;
  maxWalkMi: number;
  maxDriveMi: number;
  maxTransfers: number;
}

interface Props {
  value: Options;
  onChange: (o: Options) => void;
}

function Slider(props: {
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <input
      type="range"
      aria-label={props.label}
      className="w-full accent-blue-600 disabled:opacity-40"
      {...props}
      onChange={(e) => props.onChange(Number(e.target.value))}
    />
  );
}

export default function PlanOptions({ value: o, onChange }: Props) {
  const set = <K extends keyof Options>(k: K, v: Options[K]) => onChange({ ...o, [k]: v });

  return (
    <div className="space-y-4">
      <div>
        <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-slate-500">Leave at</label>
        <div className="flex gap-1.5">
          <input
            type="datetime-local"
            value={o.departAt}
            onChange={(e) => set("departAt", e.target.value)}
            className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500"
          />
          <button
            onClick={() => set("departAt", "")}
            className={`rounded-lg border px-3 text-sm ${
              o.departAt === "" ? "border-blue-500 bg-blue-50 text-blue-700" : "border-slate-300 text-slate-600 hover:bg-slate-50"
            }`}
          >
            Now
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 p-3">
        <div className="flex items-center justify-between text-sm">
          <span className="flex items-center gap-2 font-medium text-slate-700">
            <WalkIcon /> Max walking distance
          </span>
          <span className="tabular-nums text-slate-600">{o.maxWalkMi.toFixed(2)} mi</span>
        </div>
        <Slider label="Max walking distance" value={o.maxWalkMi} min={0.1} max={2} step={0.05}
          onChange={(v) => set("maxWalkMi", v)} />
        <p className="text-xs text-slate-500">Applies to every walking leg: to the first stop, between buses, and to your destination.</p>
      </div>

      <div className="space-y-2">
        <p className="text-xs font-medium uppercase tracking-wide text-slate-500">Get to the bus by</p>
        <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-slate-200 p-3 text-sm has-[:checked]:border-blue-300 has-[:checked]:bg-blue-50/50">
          <input type="checkbox" checked={o.walkAccess} onChange={(e) => set("walkAccess", e.target.checked)}
            className="h-4 w-4 accent-blue-600" />
          <WalkIcon className="h-5 w-5 text-slate-600" />
          <span className="font-medium text-slate-700">Walking</span>
        </label>
        <div className="rounded-xl border border-slate-200 p-3 has-[:checked]:border-violet-300 has-[:checked]:bg-violet-50/50">
          <label className="flex cursor-pointer items-center gap-3 text-sm">
            <input type="checkbox" checked={o.parkRide} onChange={(e) => set("parkRide", e.target.checked)}
              className="h-4 w-4 accent-violet-600" />
            <CarIcon className="h-5 w-5 text-slate-600" />
            <span className="flex-1 font-medium text-slate-700">Park & Ride</span>
            <span className="tabular-nums text-slate-600">{o.maxDriveMi} mi drive</span>
          </label>
          <Slider label="Max driving distance" value={o.maxDriveMi} min={1} max={40} step={1}
            disabled={!o.parkRide} onChange={(v) => set("maxDriveMi", v)} />
        </div>
      </div>

      <div className="flex items-center justify-between text-sm">
        <span className="font-medium text-slate-700">Max transfers</span>
        <div className="flex overflow-hidden rounded-lg border border-slate-300">
          {[0, 1, 2, 3].map((n) => (
            <button key={n} onClick={() => set("maxTransfers", n)}
              className={`px-3 py-1 tabular-nums ${o.maxTransfers === n ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-50"}`}>
              {n}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

import { useEffect } from "react";
import L from "leaflet";
import { Circle, CircleMarker, MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import { LEG_COLORS, formatAgo, formatDistance } from "../lib/format";
import type { Itinerary, Lot, Place, RouteInfo, Vehicle } from "../lib/types";

interface Props {
  origin: Place | null;
  destination: Place | null;
  walkRadiusM: number;
  driveRadiusM: number | null;
  lots: Lot[];
  itinerary: Itinerary | null;
  onMapClick: ((lat: number, lon: number) => void) | null;
  liveBuses: { vehicle: Vehicle; route: RouteInfo }[];
}

const esc = (t: string) => t.replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);

function busIcon(route: RouteInfo, bearing: number | null) {
  const arrow =
    bearing == null
      ? ""
      : `<span style="position:absolute;left:50%;top:50%;width:0;height:0;transform:translate(-50%,-50%) rotate(${bearing}deg) translateY(-19px);border:5px solid transparent;border-bottom:7px solid ${route.color}"></span>`;
  return L.divIcon({
    className: "",
    iconSize: [30, 22],
    iconAnchor: [15, 11],
    html: `<div style="position:relative;width:30px;height:22px">${arrow}<div style="width:30px;height:22px;border-radius:6px;background:${route.color};color:${route.text_color};border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.4);font:700 11px/18px system-ui;text-align:center;overflow:hidden">${esc(route.short_name)}</div></div>`,
  });
}

function ClickHandler({ onClick }: { onClick: Props["onMapClick"] }) {
  useMapEvents({ click: (e) => onClick?.(e.latlng.lat, e.latlng.lng) });
  return null;
}

function FitBounds({ points }: { points: [number, number][] }) {
  const map = useMap();
  const key = JSON.stringify(points);
  useEffect(() => {
    if (points.length === 1) map.flyTo(points[0], Math.max(map.getZoom(), 14), { duration: 0.6 });
    else if (points.length > 1) map.flyToBounds(L.latLngBounds(points), { padding: [40, 40], duration: 0.6 });
  }, [key]);
  return null;
}

const NJ_CENTER: [number, number] = [40.3, -74.5];

export default function MapView({ origin, destination, walkRadiusM, driveRadiusM, lots, itinerary, onMapClick, liveBuses }: Props) {
  // Include the bus's position at planning time so an approaching bus is in view (not live polls, to avoid re-fitting).
  const fit: [number, number][] = itinerary
    ? itinerary.legs.flatMap((l) => (l.vehicle ? [...l.geometry, [l.vehicle.lat, l.vehicle.lon] as [number, number]] : l.geometry))
    : [origin, destination].filter((p): p is Place => !!p).map((p) => [p.lat, p.lon]);

  return (
    <MapContainer center={NJ_CENTER} zoom={8} className="h-full w-full" zoomControl={false}>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <ClickHandler onClick={onMapClick} />
      <FitBounds points={fit} />

      {origin && driveRadiusM && (
        <Circle center={[origin.lat, origin.lon]} radius={driveRadiusM}
          pathOptions={{ color: "#7c3aed", weight: 1.5, dashArray: "6 6", fillOpacity: 0.04 }} />
      )}
      {origin && walkRadiusM > 0 && (
        <Circle center={[origin.lat, origin.lon]} radius={walkRadiusM}
          pathOptions={{ color: "#16a34a", weight: 1.5, dashArray: "4 4", fillOpacity: 0.06 }} />
      )}
      {destination && (
        <Circle center={[destination.lat, destination.lon]} radius={walkRadiusM}
          pathOptions={{ color: "#dc2626", weight: 1.5, dashArray: "4 4", fillOpacity: 0.06 }} />
      )}

      {lots.map((l) => (
        <CircleMarker key={l.id} center={[l.lat, l.lon]} radius={6}
          pathOptions={{ color: "#fff", weight: 2, fillColor: "#7c3aed", fillOpacity: 1 }}>
          <Tooltip>
            <strong>P</strong> {l.name}
            {l.capacity ? ` · ${l.capacity} spaces` : ""} · {formatDistance(l.distance_m)} away
          </Tooltip>
        </CircleMarker>
      ))}

      {itinerary?.legs.map((leg, i) =>
        leg.mode === "bus" ? (
          <Polyline key={i} positions={leg.geometry}
            pathOptions={{ color: leg.route?.color ?? "#2563eb", weight: 6, opacity: 0.9 }}>
            <Tooltip sticky>{leg.route?.short_name} {leg.headsign}</Tooltip>
          </Polyline>
        ) : (
          <Polyline key={i} positions={leg.geometry}
            pathOptions={{ color: LEG_COLORS[leg.mode], weight: 4, dashArray: leg.mode === "walk" ? "2 8" : undefined, lineCap: "round" }} />
        ),
      )}
      {itinerary?.legs
        .filter((l) => l.mode === "bus")
        .flatMap((l) => [l.from_place, l.to_place])
        .map((p, i) => (
          <CircleMarker key={`s${i}`} center={[p.lat, p.lon]} radius={5}
            pathOptions={{ color: "#0f172a", weight: 2, fillColor: "#fff", fillOpacity: 1 }}>
            <Tooltip>{p.name}</Tooltip>
          </CircleMarker>
        ))}

      {liveBuses.map(({ vehicle: v, route }) => (
        <Marker key={v.trip_id} position={[v.lat, v.lon]} icon={busIcon(route, v.bearing)} zIndexOffset={1000}>
          <Tooltip>
            Live: {route.short_name} bus {v.label ?? ""}
            {v.occupancy ? ` · ${v.occupancy}` : ""}
            {v.timestamp ? ` · ${formatAgo(v.timestamp)}` : ""}
          </Tooltip>
        </Marker>
      ))}

      {origin && (
        <CircleMarker center={[origin.lat, origin.lon]} radius={9}
          pathOptions={{ color: "#fff", weight: 3, fillColor: "#16a34a", fillOpacity: 1 }}>
          <Tooltip>Start: {origin.name}</Tooltip>
        </CircleMarker>
      )}
      {destination && (
        <CircleMarker center={[destination.lat, destination.lon]} radius={9}
          pathOptions={{ color: "#fff", weight: 3, fillColor: "#dc2626", fillOpacity: 1 }}>
          <Tooltip>End: {destination.name}</Tooltip>
        </CircleMarker>
      )}
    </MapContainer>
  );
}

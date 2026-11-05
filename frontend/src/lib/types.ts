export interface Place {
  name: string;
  lat: number;
  lon: number;
  stop_id?: string | null;
}

export interface RouteInfo {
  id: string;
  short_name: string;
  long_name: string;
  color: string;
  text_color: string;
}

export interface StopVisit {
  name: string;
  lat: number;
  lon: number;
  time: string;
}

export interface Leg {
  mode: "walk" | "drive" | "bus";
  start: string;
  end: string;
  from_place: Place;
  to_place: Place;
  distance_m: number;
  geometry: [number, number][];
  route: RouteInfo | null;
  headsign: string | null;
  trip_id: string | null;
  intermediate_stops: StopVisit[];
  realtime: boolean;
  scheduled_start: string | null;
  scheduled_end: string | null;
  delay_s: number | null;
  vehicle: Vehicle | null;
  alerts: Alert[];
}

export interface Vehicle {
  trip_id: string;
  route_id: string | null;
  lat: number;
  lon: number;
  bearing: number | null;
  timestamp: number | null;
  label: string | null;
  occupancy: string | null;
}

export interface Alert {
  id: string;
  header: string;
  description: string;
  url: string | null;
  route_ids: string[];
}

export interface RealtimeStatus {
  enabled: boolean;
  last_update: string | null;
  feed_time: string | null;
  trips_with_predictions: number;
  canceled_trips: number;
  vehicles: number;
  alerts: number;
  error: string | null;
}

export interface Itinerary {
  access_mode: "walk" | "park_ride" | "walk_only";
  start: string;
  end: string;
  duration_s: number;
  transfers: number;
  walk_m: number;
  drive_m: number;
  wait_s: number;
  park_ride_lot: string | null;
  legs: Leg[];
}

export interface PlanResponse {
  itineraries: Itinerary[];
  notes: string[];
}

export interface PlanRequest {
  origin: { lat: number; lon: number; name?: string };
  destination: { lat: number; lon: number; name?: string };
  depart_at: string | null;
  arrive_by: string | null;
  walk_access: boolean;
  park_ride: boolean;
  max_walk_m: number;
  max_drive_m: number;
  max_transfers: number;
  num_itineraries: number;
}

export interface Lot {
  id: string;
  name: string;
  lat: number;
  lon: number;
  capacity: number | null;
  operator: string | null;
  distance_m: number;
}

export interface Health {
  using_sample_data: boolean;
  stops: number;
  routes: number;
  trips: number;
  feed_start: string | null;
  feed_end: string | null;
  park_ride_lots: number;
  use_osrm: boolean;
  realtime: RealtimeStatus;
}

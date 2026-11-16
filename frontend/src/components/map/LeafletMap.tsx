import { useEffect, useMemo } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Circle, CircleMarker, MapContainer, Marker, Polyline, TileLayer, Tooltip, useMap } from "react-leaflet";
import type { LatLng } from "../../types";
import type { MapViewProps } from "./types";

const icons = new Map<string, L.DivIcon>();

/** One icon per look, made once: a new icon object on every render makes Leaflet rebuild the marker's element. */
function pin(label: string, color: string, big: boolean) {
  const key = `${label}|${color}|${big}`;
  const cached = icons.get(key);
  if (cached) return cached;
  const size = big ? 30 : 24;
  const icon = L.divIcon({
    className: "map-pin",
    html: `<div style="background:${color};width:${size}px;height:${size}px;line-height:${size}px">${label}</div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
  icons.set(key, icon);
  return icon;
}

function FitBounds({ points, selected }: { points: [number, number][]; selected: LatLng | null }) {
  const map = useMap();
  const key = points.map((p) => p.join(",")).join("|");
  useEffect(() => {
    if (points.length) map.fitBounds(L.latLngBounds(points), { padding: [30, 30], maxZoom: 15 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  // keyed on the coordinates, not the object, so unrelated re-renders don't pull the view back
  const lat = selected?.lat;
  const lng = selected?.lng;
  useEffect(() => {
    if (lat !== undefined && lng !== undefined) map.setView([lat, lng], Math.max(map.getZoom(), 15));
  }, [lat, lng, map]);
  return null;
}

/** Fallback interactive map (OpenStreetMap tiles) used when no Google Maps browser key is configured. */
export default function LeafletMap({ markers, polylines, current, selectedId, onSelect }: MapViewProps) {
  const points = useMemo(() => markers.map((m) => [m.position.lat, m.position.lng] as [number, number]), [markers]);
  const lines = useMemo(() => polylines.map((p) => ({ ...p, positions: p.path.map((x) => [x.lat, x.lng] as [number, number]) })),
    [polylines]);
  const handlers = useMemo(() => new Map(markers.map((m) => [m.id, { click: () => onSelect?.(m.id) }])), [markers, onSelect]);
  const sel = markers.find((m) => m.id === selectedId);
  const center: [number, number] = points[0] ?? [20, 0];
  return (
    <MapContainer center={center} zoom={13} className="map" scrollWheelZoom>
      <TileLayer attribution='&copy; OpenStreetMap contributors'
                 url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
      <FitBounds points={points} selected={sel?.position ?? null} />
      {lines.map((p) => (
        <Polyline key={p.id} positions={p.positions}
                  pathOptions={{ color: p.color, weight: 4, opacity: 0.75, dashArray: p.dashed ? "6 8" : undefined }} />
      ))}
      {markers.map((m) => (
        <Marker key={m.id} position={[m.position.lat, m.position.lng]}
                icon={pin(m.label, m.color, m.kind === "home" || m.id === selectedId)}
                eventHandlers={handlers.get(m.id)}>
          <Tooltip>{m.title}</Tooltip>
        </Marker>
      ))}
      {current && (
        <>
          {current.accuracy && <Circle center={[current.position.lat, current.position.lng]} radius={current.accuracy}
                                       pathOptions={{ color: "#0ea5e9", weight: 1, fillOpacity: 0.1 }} />}
          <CircleMarker center={[current.position.lat, current.position.lng]} radius={8}
                        pathOptions={{ color: "#fff", weight: 3, fillColor: "#0ea5e9", fillOpacity: 1 }}>
            <Tooltip>You are here</Tooltip>
          </CircleMarker>
        </>
      )}
    </MapContainer>
  );
}

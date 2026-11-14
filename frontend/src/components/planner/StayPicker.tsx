import { useEffect, useMemo, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { MapContainer, Marker, TileLayer, useMapEvents } from "react-leaflet";
import { MapPin } from "lucide-react";
import { api } from "../../services/api";
import type { AddressSuggestion, LatLng } from "../../types";

interface Props {
  center: LatLng;               // the destination, or the stay already pinned
  initial: LatLng | null;
  onPick: (point: LatLng, place: AddressSuggestion | null) => void;
}

const PIN = L.divIcon({ className: "stay-pin", html: "<span>H</span>", iconSize: [34, 34], iconAnchor: [17, 17] });

function ClickToPin({ onPoint }: { onPoint: (p: LatLng) => void }) {
  useMapEvents({ click: (e) => onPoint({ lat: e.latlng.lat, lng: e.latlng.lng }) });
  return null;
}

type Lookup = { state: "idle" } | { state: "busy" } | { state: "done"; place: AddressSuggestion | null } | { state: "failed" };

/** A map of the destination: tap where you are staying (or drag the pin), and the planner starts every day there.
    Loaded only when the traveller opens it, so Leaflet stays out of the home page's first download. */
export default function StayPicker({ center, initial, onPick }: Props) {
  const [point, setPoint] = useState<LatLng | null>(initial);
  const [lookup, setLookup] = useState<Lookup>({ state: "idle" });

  // name the point once the pin settles; the name is a label only, the point is what the plan uses
  useEffect(() => {
    if (!point) return;
    const ctrl = new AbortController();
    setLookup({ state: "busy" });
    const t = window.setTimeout(() => {
      api.reverseLookup(point, ctrl.signal)
        .then((r) => setLookup({ state: "done", place: r.result }))
        .catch(() => { if (!ctrl.signal.aborted) setLookup({ state: "failed" }); });
    }, 350);
    return () => { window.clearTimeout(t); ctrl.abort(); };
  }, [point]);

  const handlers = useMemo(() => ({
    dragend: (e: L.LeafletEvent) => {
      const p = (e.target as L.Marker).getLatLng();
      setPoint({ lat: p.lat, lng: p.lng });
    },
  }), []);

  const place = lookup.state === "done" ? lookup.place : null;
  return (
    <div className="stay-picker">
      <p className="small muted">Tap the map where you are staying, then drag the pin to adjust.</p>
      <MapContainer center={[center.lat, center.lng]} zoom={initial ? 16 : 13} className="map stay-map" scrollWheelZoom>
        <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <ClickToPin onPoint={setPoint} />
        {point && <Marker position={[point.lat, point.lng]} icon={PIN} draggable eventHandlers={handlers} />}
      </MapContainer>
      <div className="stay-picker-foot">
        <p className="small" role="status" aria-live="polite">
          {!point ? "No spot picked yet."
            : lookup.state === "busy" || lookup.state === "idle" ? "Looking up the address..."
            : place ? <>Near <strong>{place.label}</strong></>
            : lookup.state === "failed" ? "The address lookup is unavailable; the spot will be used as it is."
            : "No address is known here; the spot will be used as it is."}
          {point && <span className="muted num"> · {point.lat.toFixed(5)}, {point.lng.toFixed(5)}</span>}
        </p>
        <button type="button" className="btn btn-cta" disabled={!point}
                onClick={() => point && onPick(point, place)}>
          <MapPin size={16} aria-hidden /> Stay here
        </button>
      </div>
    </div>
  );
}

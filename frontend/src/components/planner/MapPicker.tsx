import { useEffect, useMemo, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { MapContainer, Marker, TileLayer, Tooltip, useMapEvents } from "react-leaflet";
import { MapPin } from "lucide-react";
import { api } from "../../services/api";
import type { AddressSuggestion, LatLng } from "../../types";

export interface MapPickerProps {
  center: LatLng;                 // the destination, or the point already picked
  initial: LatLng | null;
  hint: string;                   // what to tap
  confirmLabel: string;           // the button that takes the point
  pinText: string;                // one or two characters on the new pin ("H" for the stay, a number for a sight)
  others?: { at: LatLng; label: string; text: string }[];   // points already chosen, shown for orientation
  onPick: (point: LatLng, place: AddressSuggestion | null) => void;
}

const pin = (text: string, cls: string) =>
  L.divIcon({ className: cls, html: `<span>${text.replace(/[^\w]/g, "").slice(0, 2)}</span>`, iconSize: [34, 34], iconAnchor: [17, 17] });

function ClickToPin({ onPoint }: { onPoint: (p: LatLng) => void }) {
  // wrap(): a map panned past the date line reports longitudes like 181.2, which the API rightly refuses
  useMapEvents({ click: (e) => { const p = e.latlng.wrap(); onPoint({ lat: p.lat, lng: p.lng }); } });
  return null;
}

type Lookup = { state: "idle" } | { state: "busy" } | { state: "done"; place: AddressSuggestion | null } | { state: "failed" };

/** A map of the destination: tap a spot (or drag the pin) and its nearest address is looked up. Used for the stay and
    for must-see places. Loaded only when the traveller opens it, so Leaflet stays out of the home page's first download. */
export default function MapPicker({ center, initial, hint, confirmLabel, pinText, others = [], onPick }: MapPickerProps) {
  const [point, setPoint] = useState<LatLng | null>(initial);
  const [lookup, setLookup] = useState<Lookup>({ state: "idle" });
  const icon = useMemo(() => pin(pinText, "stay-pin"), [pinText]);
  const otherIcons = useMemo(() => others.map((o) => pin(o.text, "stay-pin is-other")), [others]);

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
      const p = (e.target as L.Marker).getLatLng().wrap();
      setPoint({ lat: p.lat, lng: p.lng });
    },
  }), []);

  const place = lookup.state === "done" ? lookup.place : null;
  return (
    <div className="stay-picker">
      <p className="small muted">{hint}</p>
      <MapContainer center={[center.lat, center.lng]} zoom={initial ? 16 : 13} className="map stay-map" scrollWheelZoom>
        <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <ClickToPin onPoint={setPoint} />
        {others.map((o, i) => (
          <Marker key={`${o.at.lat},${o.at.lng},${i}`} position={[o.at.lat, o.at.lng]} icon={otherIcons[i]} interactive>
            <Tooltip>{o.label}</Tooltip>
          </Marker>
        ))}
        {point && <Marker position={[point.lat, point.lng]} icon={icon} draggable eventHandlers={handlers} />}
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
        <button type="button" className="btn btn-cta" disabled={!point} onClick={() => point && onPick(point, place)}>
          <MapPin size={16} aria-hidden /> {confirmLabel}
        </button>
      </div>
    </div>
  );
}

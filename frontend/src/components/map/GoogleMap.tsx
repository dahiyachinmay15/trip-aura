import { useEffect, useRef, useState } from "react";
import type { MapMarker, MapViewProps } from "./types";

let loader: Promise<void> | null = null;

/** Loads the Maps JavaScript API once. Requires VITE_GOOGLE_MAPS_API_KEY (a browser-restricted key). */
function loadGoogleMaps(key: string): Promise<void> {
  if (window.google?.maps) return Promise.resolve();
  if (loader) return loader;
  loader = new Promise((resolve, reject) => {
    const cb = "__tripauraMapsReady";
    (window as unknown as Record<string, () => void>)[cb] = () => resolve();
    const s = document.createElement("script");
    s.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&callback=${cb}&loading=async`;
    s.async = true;
    s.onerror = () => { loader = null; reject(new Error("Failed to load Google Maps JavaScript API")); };
    document.head.appendChild(s);
  });
  return loader;
}

function markerIcon(mk: MapMarker, big: boolean): google.maps.Symbol {
  return { path: google.maps.SymbolPath.CIRCLE, scale: big ? 14 : 11, fillColor: mk.color, fillOpacity: 1,
           strokeColor: "#fff", strokeWeight: 2 };
}

export default function GoogleMap({ markers, polylines, current, selectedId, onSelect, apiKey }:
  MapViewProps & { apiKey: string }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<google.maps.Map | null>(null);
  const overlays = useRef<{ setMap: (m: google.maps.Map | null) => void }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const traffic = useRef<google.maps.TrafficLayer | null>(null);
  const markerById = useRef(new Map<string, { marker: google.maps.Marker; spec: MapMarker }>());
  const shownSelection = useRef<string | null>(null);
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  useEffect(() => {
    loadGoogleMaps(apiKey)
      .then(() => {
        if (el.current && !map.current) {
          map.current = new google.maps.Map(el.current, {
            center: { lat: 20, lng: 0 }, zoom: 13, mapTypeControl: false,
          });
        }
        if (map.current) {
          traffic.current ??= new google.maps.TrafficLayer();
          traffic.current.setMap(map.current);
        }
        setReady(true);
      })
      .catch((e: Error) => setError(e.message));
    return () => {
      traffic.current?.setMap(null);
      overlays.current.forEach((o) => o.setMap(null));
      overlays.current = [];
      markerById.current.clear();
    };
  }, [apiKey]);

  // the plan's markers and lines: rebuilt only when the plan (or the day shown) changes, then the view fits them
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    overlays.current.forEach((o) => o.setMap(null));
    overlays.current = [];
    markerById.current.clear();
    const bounds = new google.maps.LatLngBounds();
    for (const p of polylines) {
      const line = new google.maps.Polyline({
        path: p.path, strokeColor: p.color, strokeOpacity: p.dashed ? 0 : 0.8, strokeWeight: 4, map: m,
        icons: p.dashed ? [{ icon: { path: "M 0,-1 0,1", strokeOpacity: 0.9, scale: 3 }, offset: "0", repeat: "14px" }] : undefined,
      });
      overlays.current.push(line);
    }
    for (const mk of markers) {
      const marker = new google.maps.Marker({
        position: mk.position, map: m, title: mk.title,
        label: { text: mk.label, color: "#ffffff", fontSize: "11px", fontWeight: "700" },
        icon: markerIcon(mk, mk.kind === "home"),
      });
      marker.addListener("click", () => selectRef.current?.(mk.id));
      overlays.current.push(marker);
      markerById.current.set(mk.id, { marker, spec: mk });
      bounds.extend(mk.position);
    }
    if (current) {
      overlays.current.push(new google.maps.Marker({
        position: current.position, map: m, title: "You are here",
        icon: { path: google.maps.SymbolPath.CIRCLE, scale: 8, fillColor: "#0ea5e9", fillOpacity: 1,
                strokeColor: "#fff", strokeWeight: 3 },
      }));
      if (current.accuracy) {
        overlays.current.push(new google.maps.Circle({ center: current.position, radius: current.accuracy, map: m,
          strokeColor: "#0ea5e9", strokeWeight: 1, fillColor: "#0ea5e9", fillOpacity: 0.1 }));
      }
    }
    shownSelection.current = null;
    if (!bounds.isEmpty()) m.fitBounds(bounds, 30);
  }, [ready, markers, polylines, current]);

  // a selection enlarges its marker (and shrinks the previous one) and pans to it; nothing is rebuilt
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    const prev = shownSelection.current ? markerById.current.get(shownSelection.current) : undefined;
    if (prev) prev.marker.setIcon(markerIcon(prev.spec, prev.spec.kind === "home"));
    const sel = selectedId ? markerById.current.get(selectedId) : undefined;
    shownSelection.current = sel ? selectedId ?? null : null;
    if (!sel) return;
    sel.marker.setIcon(markerIcon(sel.spec, true));
    m.panTo(sel.spec.position);
    m.setZoom(Math.max(m.getZoom() ?? 13, 15));
  }, [ready, selectedId, markers]);

  if (error) return <div className="map map-error">{error}. Check VITE_GOOGLE_MAPS_API_KEY.</div>;
  return <div ref={el} className="map" />;
}

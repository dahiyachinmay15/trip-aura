import { useId } from "react";

/**
 * Brand mark: a compass whose needle points north-east, its route leaving the ring for a paper plane.
 * `full` adds the sun rising in the ring's opening, the wave the ring runs into and the trail of dots; it is for
 * sizes of about 40px and up. The small drawing (ring, needle, plane, heavier lines) is the same as
 * public/favicon.svg; the full one is the same as the app icons in public/.
 */
export default function Logo({ size = 26, full = false }: { size?: number; full?: boolean }) {
  const id = `ta${useId().replace(/[^a-zA-Z0-9]/g, "")}`;   // one gradient per mark: the header and footer both draw one
  const gradient = (
    <defs>
      <linearGradient id={id} x1="12" y1="12" x2="50" y2="56" gradientUnits="userSpaceOnUse">
        <stop offset="0" stopColor="#e2733f" />
        <stop offset=".45" stopColor="#cf5a36" />
        <stop offset=".7" stopColor={full ? "#2c6e6b" : "#1f6668"} />
        <stop offset="1" stopColor="#0e5a5f" />
      </linearGradient>
    </defs>
  );
  const common = { width: size, height: size, viewBox: "0 0 64 64", fill: "none", strokeLinecap: "round" as const,
                   strokeLinejoin: "round" as const, "aria-hidden": true, className: "logo" };

  if (!full) {
    return (
      <svg {...common}>
        {gradient}
        <path d="M40.6 18.8A20 20 0 1 0 48.6 38.6" stroke={`url(#${id})`} strokeWidth="5.4" />
        <path d="M39.6 23.4 31.4 38.6 24.4 31.6Z" fill="#d5532a" stroke="#d5532a" strokeWidth="2.4" />
        <path d="M16.4 46.6 24.4 31.6 31.4 38.6Z" fill="#0e5a5f" stroke="#0e5a5f" strokeWidth="2.4" />
        <path d="M61 3 44.5 9.6 50.4 13.6 54.4 19.5Z" fill="#d5532a" stroke="#d5532a" strokeWidth="2.6" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      {gradient}
      {/* the ring, open on the right where the route leaves it, runs on into the wave */}
      <path d="M38.9 18.4A19 19 0 1 0 46.7 37.3c2.4-2.4 4.8-2.4 7.2 0s4.8 2.4 7.2 0" stroke={`url(#${id})`} strokeWidth="3.2" />
      <circle cx="28" cy="10.4" r="2.7" stroke="#df6c3d" strokeWidth="2.4" />
      <path d="M37.9 24.1 31 37 25 31Z" fill="#d5532a" stroke="#d5532a" strokeWidth="1.4" />
      <path d="M18.1 43.9 25 31 31 37Z" stroke="#0e5a5f" strokeWidth="2.2" />
      <circle cx="28" cy="34" r="2.3" fill="#f6f4ef" stroke="#0e5a5f" strokeWidth="1.8" />
      <path d="M41.4 20.6 46.4 15.6" stroke="#d5532a" strokeWidth="2.8" strokeDasharray="0 4.6" />
      <path d="M60 4 46.5 9.5 51.5 12.5 54.5 17.5Z M60 4 51.5 12.5" stroke="#d5532a" strokeWidth="2.4" />
      <path d="M45.8 31a4.2 4.2 0 0 1 8.4 0" stroke="#e8913e" strokeWidth="2.4" />
      <path d="M50 24.4v-2M44.6 26.6l-1.5-1.4M55.4 26.6l1.5-1.4" stroke="#e8913e" strokeWidth="2.2" />
      <path d="M4.1 36.1A24 24 0 0 0 16 54.8" stroke="#2c6e6b" strokeWidth="2.6" strokeDasharray="0 4.6" />
    </svg>
  );
}

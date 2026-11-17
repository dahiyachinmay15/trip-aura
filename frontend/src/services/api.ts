import type { AddressSuggestion, ApiErrorBody, ApiOptions, ApiStatus, CitySuggestion, Itinerary, LatLng, ParsedTrip, TripRequest } from "../types";

// Empty in production when Vercel rewrites /api/* to the backend, and locally (Vite proxy).
const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown,
              /** the server's X-Request-ID, to quote when reporting a failure */ public requestId?: string) {
    super(message);
  }
}

interface Options extends RequestInit {
  /** give up after this long (a new city can take 20 s, and a sleeping server a minute to start) */
  timeoutMs?: number;
  /** retries for a GET after a network error or 502/503/504, with exponential backoff and jitter */
  retries?: number;
}

const GET_TIMEOUT_MS = 20_000;
const RETRYABLE = new Set([502, 503, 504]);
const inFlight = new Map<string, Promise<unknown>>();   // identical GETs share one request

const sleep = (ms: number, signal?: AbortSignal | null) => new Promise<void>((resolve, reject) => {
  const t = window.setTimeout(resolve, ms);
  signal?.addEventListener("abort", () => { window.clearTimeout(t); reject(signal.reason); }, { once: true });
});
/** about 400 ms, 800 ms, 1.6 s..., each randomised so many browsers coming back at once do not retry in step */
const backoff = (attempt: number) => Math.min(4000, 400 * 2 ** attempt) * (0.5 + Math.random() / 2);

/** The caller's signal (a newer keystroke cancels an old search) combined with a timeout. */
function withTimeout(signal: AbortSignal | null | undefined, ms: number): { signal: AbortSignal; timedOut: () => boolean; done: () => void } {
  const ctrl = new AbortController();
  let timedOut = false;
  const t = window.setTimeout(() => { timedOut = true; ctrl.abort(new DOMException("Timed out", "TimeoutError")); }, ms);
  const onAbort = () => ctrl.abort(signal?.reason);
  if (signal?.aborted) onAbort(); else signal?.addEventListener("abort", onAbort, { once: true });
  return { signal: ctrl.signal, timedOut: () => timedOut, done: () => { window.clearTimeout(t); signal?.removeEventListener("abort", onAbort); } };
}

async function once<T>(path: string, init: Options, method: string): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init.headers as Record<string, string> ?? {}) };
  const timer = withTimeout(init.signal, init.timeoutMs ?? GET_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, method, headers, signal: timer.signal });
  } catch (e) {
    if (timer.timedOut()) {
      throw new ApiError(0, "timeout", "The TripAura server took too long to answer. Check your connection and try again.");
    }
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, "network_error", navigator.onLine === false
      ? "You're offline. Connect to the internet and try again."
      : "Can't reach the TripAura server. Check your connection and try again.");
  } finally {
    timer.done();
  }
  const requestId = res.headers.get("X-Request-ID") ?? undefined;
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    const err = (body as ApiErrorBody | null)?.error;
    const retry = Number(res.headers.get("Retry-After")) || undefined;
    let message = res.status === 429
      ? `Too many requests. Try again in ${retry ?? "a few"} seconds.`
      : res.status === 413 ? "That request is too large to send."
      : err?.message ?? `Request failed (${res.status})`;
    // a server failure gets a short reference, so it can be found in the server's logs
    if (res.status >= 500 && requestId) message += ` (reference ${requestId.slice(0, 8)})`;
    throw new ApiError(res.status, err?.code ?? "http_error", message, err?.details, requestId);
  }
  return body as T;
}

async function request<T>(path: string, init: Options = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  if (method !== "GET") return once<T>(path, init, method);   // never repeat a request that may change or cost something

  // a GET nobody can cancel is shared with an identical one already on its way (two components asking for options)
  const shared = !init.signal;
  if (shared && inFlight.has(path)) return inFlight.get(path) as Promise<T>;
  const run = (async () => {
    const retries = init.retries ?? 2;
    for (let attempt = 0; ; attempt++) {
      try {
        return await once<T>(path, init, method);
      } catch (e) {
        const err = e as ApiError;
        const transient = err instanceof ApiError && (err.code === "network_error" || RETRYABLE.has(err.status));
        if (!transient || attempt >= retries || navigator.onLine === false) throw e;
        await sleep(backoff(attempt), init.signal);
      }
    }
  })();
  if (!shared) return run;
  inFlight.set(path, run);
  try {
    return await run;
  } finally {
    inFlight.delete(path);
  }
}

export const api = {
  // the app's own wake-up loop retries this one slowly while a sleeping server starts
  status: () => request<ApiStatus>("/api/meta/status", { retries: 0 }),
  options: () => request<ApiOptions>("/api/meta/options"),
  // a first visit to a city takes 10 to 20 s, a server waking from sleep up to a minute more
  previewTrip: (req: TripRequest) =>
    request<Itinerary>("/api/trips/preview", { method: "POST", body: JSON.stringify(req), timeoutMs: 150_000 }),
  citySuggestions: (q: string, signal?: AbortSignal) =>
    request<{ results: CitySuggestion[] }>(`/api/geo/cities?q=${encodeURIComponent(q)}`, { signal, retries: 1 }),
  // `city` keeps results in the destination: typing "inn" finds the inns there, not a river abroad
  addressSuggestions: (q: string, near?: LatLng | null, city?: string, signal?: AbortSignal) =>
    request<{ results: AddressSuggestion[] }>(
      `/api/geo/addresses?q=${encodeURIComponent(q)}${near ? `&lat=${near.lat}&lng=${near.lng}` : ""}`
        + (city ? `&city=${encodeURIComponent(city)}` : ""), { signal, retries: 1 }),
  reverseLookup: (at: LatLng, signal?: AbortSignal) =>
    request<{ result: AddressSuggestion | null }>(`/api/geo/reverse?lat=${at.lat}&lng=${at.lng}`, { signal, retries: 1 }),
  parseTrip: (text: string) =>
    request<ParsedTrip>("/api/nlp/parse-trip", { method: "POST", body: JSON.stringify({ text }) }),
};

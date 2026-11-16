import { useEffect, useState } from "react";
import type { ApiOptions, Pace, TripRequest, TripStyle, TransportMode } from "../../types";
import { MODE_LABEL, pretty } from "../../utils";
import ChipSelect from "../planner/ChipSelect";
import NumberInput from "../ui/NumberInput";
import RetryNotice from "../ui/RetryNotice";
import Sheet from "../ui/Sheet";
import { SkeletonLines } from "../ui/Skeleton";
import Stepper from "../ui/Stepper";
import GeneratingPanel from "../planner/GeneratingPanel";

const STYLE: Record<TripStyle, { label: string; hint: string }> = {
  budget: { label: "Lowest cost", hint: "Cheaper meals and free or cheap sights." },
  mid: { label: "Mid-range", hint: "Balance cost, comfort and time." },
  premium: { label: "Premium", hint: "Better meals and faster rides, within your budget." },
};
const PACE_HINT: Record<Pace, string> = {
  relaxed: "Fewer stops and longer breaks.",
  moderate: "A full day with room to breathe.",
  packed: "As much as the day can hold.",
};

interface Props {
  open: boolean;
  onClose: () => void;
  itinerary: { request: TripRequest };
  options: ApiOptions | null;
  loadingOptions: boolean;
  optionsError: boolean;
  onRetryOptions: () => void;
  busy: boolean;
  onSave: (request: TripRequest) => void;
}

/** Quick edits to the saved request; submitting builds a fresh itinerary from those preferences. */
export default function EditItinerarySheet({
  open, onClose, itinerary, options, loadingOptions, optionsError, onRetryOptions, busy, onSave,
}: Props) {
  const [draft, setDraft] = useState(itinerary.request);
  useEffect(() => {
    if (open) setDraft(itinerary.request);
  }, [open, itinerary]);

  const set = <K extends keyof TripRequest>(key: K, value: TripRequest[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const toggleMode = (mode: TransportMode) => set(
    "transport_modes",
    draft.transport_modes.includes(mode)
      ? draft.transport_modes.filter((item) => item !== mode)
      : [...draft.transport_modes, mode],
  );
  const validBudget = draft.budget > draft.accommodation_cost_total;

  return (
    <Sheet open={open} onClose={onClose} title="Edit your itinerary">
      {busy ? (
        <GeneratingPanel city={draft.destination_city} firstVisitHint={false} />
      ) : loadingOptions ? (
        <div className="tile"><SkeletonLines lines={7} /></div>
      ) : optionsError || !options ? (
        <RetryNotice message="Couldn't load the trip options." onRetry={onRetryOptions} />
      ) : (
        <form className="edit-trip-form" onSubmit={(event) => { event.preventDefault(); if (validBudget) onSave(draft); }}>
          <p className="small muted">Change a few preferences and rebuild this saved plan. Your current plan stays in place until the new one is ready.</p>

          <div className="grid-2">
            <div className="field">
              <label htmlFor="edit-days">Days</label>
              <Stepper id="edit-days" label="days" min={1} max={14} value={draft.num_days} onValue={(v) => set("num_days", v)} />
            </div>
            <div className="field">
              <label htmlFor="edit-travelers">Travellers</label>
              <Stepper id="edit-travelers" label="travellers" min={1} max={20} value={draft.travelers}
                       onValue={(v) => set("travelers", v)} />
            </div>
          </div>

          <div className="grid-2">
            <label className="field">
              <span>First day</span>
              <input type="date" value={draft.start_date ?? ""} onChange={(e) => set("start_date", e.target.value || null)} />
            </label>
            <label className="field">
              <span>Total budget ({draft.currency})</span>
              <NumberInput min={1} required value={draft.budget} onValue={(v) => set("budget", v)} />
            </label>
          </div>
          {!validBudget && <p className="field-error" role="alert">The trip budget must be higher than the stay cost already included.</p>}

          <fieldset className="field">
            <legend>How to spend it</legend>
            <div className="cards-3">
              {options.trip_styles.map((style) => (
                <button type="button" key={style} className="pick" aria-pressed={(draft.trip_style ?? "mid") === style}
                        onClick={() => set("trip_style", style)}>
                  <strong>{STYLE[style].label}</strong><span>{STYLE[style].hint}</span>
                </button>
              ))}
            </div>
          </fieldset>

          <fieldset className="field">
            <legend>Pace</legend>
            <div className="cards-3">
              {options.paces.map((pace) => (
                <button type="button" key={pace} className="pick" aria-pressed={draft.pace === pace}
                        onClick={() => set("pace", pace)}>
                  <strong>{pretty(pace)}</strong><span>{PACE_HINT[pace]}</span>
                </button>
              ))}
            </div>
          </fieldset>

          <ChipSelect label="Interests" options={options.interests} value={draft.interests}
                      onChange={(value) => set("interests", value)} placeholder="Add another interest" />
          <ChipSelect label="Food preferences" options={options.food_preferences} value={draft.food_preferences}
                      onChange={(value) => set("food_preferences", value)} placeholder="Add a dietary preference" />
          <ChipSelect label="Food styles" options={options.food_categories} value={draft.food_categories}
                      onChange={(value) => set("food_categories", value)} placeholder="Add a food style" />

          <fieldset className="field">
            <legend>Transport you're happy to use</legend>
            <div className="chips">
              {options.transport_modes.map((mode) => (
                <button type="button" key={mode} className={`chip ${draft.transport_modes.includes(mode) ? "chip-on" : ""}`}
                        aria-pressed={draft.transport_modes.includes(mode)} onClick={() => toggleMode(mode)}>
                  {MODE_LABEL[mode]}
                </button>
              ))}
            </div>
          </fieldset>

          <label className="check">
            <input type="checkbox" checked={draft.fit_to_budget} onChange={(e) => set("fit_to_budget", e.target.checked)} />
            Swap in cheaper options if the plan goes over budget
          </label>
          <label className="check">
            <input type="checkbox" checked={draft.return_to_accommodation}
                   onChange={(e) => set("return_to_accommodation", e.target.checked)} />
            End each day back at my stay
          </label>

          <div className="edit-trip-actions">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-cta" disabled={!validBudget}>
              Rebuild itinerary
            </button>
          </div>
        </form>
      )}
    </Sheet>
  );
}

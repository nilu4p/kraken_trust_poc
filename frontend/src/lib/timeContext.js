import { createContext, useContext } from "react";

/**
 * Reference instant of the whole situation. ``at`` = ISO UTC instant of a replay,
 * null in live mode. Every component that fetches or shows time-dependent data
 * reads it, so that all layers and panels describe the same instant.
 */
export const TimeContext = createContext({ at: null });

export const useReferenceTime = () => useContext(TimeContext);

/** "2026-10-19 21:34 Z" */
export function formatInstant(value) {
  if (!value) return "—";
  return new Date(value).toISOString().slice(0, 16).replace("T", " ") + " Z";
}

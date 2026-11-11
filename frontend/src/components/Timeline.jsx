import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { History, Pause, Play, Radio, SkipBack, SkipForward, StepBack, StepForward } from "lucide-react";
import { fetchTimeline } from "../services/api.js";
import { formatInstant } from "../lib/timeContext.js";

const HOUR = 3600 * 1000;
const DAY = 24 * HOUR;

// One lane per source of the fused picture, each on its OWN scale (dozens of vessels vs a
// handful of detections never share an axis). Hues validated for the dark panel (lightness
// band, chroma, adjacent CVD separation) in this lane order; lanes are labelled, so identity
// never relies on colour alone.
export const LANES = [
  { key: "vessels", label: "Navires AIS", color: "#16a34a" },
  { key: "scenes", label: "Scènes radar analysées", color: "#0284c7" },
  { key: "radar", label: "Détections radar", color: "#dc2626" },
  { key: "incidents_opened", label: "Incidents ouverts", color: "#9333ea" },
  { key: "rules", label: "Règles 24/7", color: "#d97706" },
  { key: "vms", label: "Positions VMS", color: "#0d9488" },
];

const SPANS = [
  { days: 1, label: "24 h" },
  { days: 7, label: "7 j" },
  { days: 30, label: "30 j" },
  { days: 90, label: "90 j" },
];

// Replay speed: simulated time per real second.
const SPEEDS = [
  { ms: 10 * 60 * 1000, label: "10 min/s" },
  { ms: HOUR, label: "1 h/s" },
  { ms: 6 * HOUR, label: "6 h/s" },
  { ms: DAY, label: "1 j/s" },
];

// Collection lane: share of each period during which an AIS feed was listening. Neutral grey
// (a measure of the instrument, not a source); periods with no listening at all are hatched
// in red: there, an empty sea means "no data", not "no vessel".
const COLLECTION = { key: "ais_listening_minutes", label: "Collecte AIS", color: "#64748b", gap: "#ef4444" };

const LANE_H = 11;
const LANE_GAP = 2;
const LABEL_W = 132;
const AXIS_H = 14;

function toInputValue(ms) {
  return new Date(ms).toISOString().slice(0, 16);
}

function bucketMs(t) {
  // "2026-10-19T21" (hour) or "2026-10-19" (day), UTC
  return Date.parse(t.length === 13 ? `${t}:00:00Z` : `${t}T00:00:00Z`);
}

/**
 * Time cursor of the situation: live / replay, step, play, and an activity strip
 * (one lane per source) to see where things happened and jump there.
 */
export default function Timeline({ at, onChange, spanDays, onSpanChange, retentionDays = 90 }) {
  const [now, setNow] = useState(Date.now());
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [width, setWidth] = useState(800);
  const [hover, setHover] = useState(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(SPEEDS[1].ms);
  const wrapRef = useRef(null);
  const dragging = useRef(false);
  const live = at == null;
  const cursor = live ? now : at;
  const oldest = now - retentionDays * DAY;
  const start = Math.max(now - spanDays * DAY, oldest);

  // The right edge is "now": refresh it (and the strip) every minute.
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    let alive = true;
    fetchTimeline(new Date(start).toISOString(), null, spanDays <= 7 ? "hour" : "day")
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError("");
      })
      .catch(() => alive && setError("Frise indisponible"));
    return () => {
      alive = false;
    };
    // Re-fetch on span change and each minute (now), not on every cursor move.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spanDays, Math.floor(now / 60000)]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(240, entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Keep the cursor visible when the span shrinks.
  useEffect(() => {
    if (!live && at < start) onSpanChange(SPANS.find((s) => now - s.days * DAY <= at)?.days || 90);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [at]);

  const plotW = Math.max(40, width - LABEL_W);
  const x = useCallback((ms) => LABEL_W + ((ms - start) / (now - start)) * plotW, [start, now, plotW]);
  const msAt = useCallback((px) => start + ((px - LABEL_W) / plotW) * (now - start), [start, now, plotW]);

  const setInstant = useCallback(
    (ms) => {
      const clamped = Math.min(Math.max(ms, oldest), Date.now());
      // Within the last two minutes = live.
      onChange(Date.now() - clamped < 2 * 60 * 1000 ? null : Math.round(clamped / 1000) * 1000);
    },
    [oldest, onChange],
  );

  // Replay: advance the cursor; reaching "now" switches back to live.
  useEffect(() => {
    if (!playing || live) return undefined;
    const timer = setInterval(() => {
      const next = at + speed;
      if (next >= Date.now() - 2 * 60 * 1000) {
        setPlaying(false);
        onChange(null);
      } else onChange(next);
    }, 1000);
    return () => clearInterval(timer);
  }, [playing, live, at, speed, onChange]);
  useEffect(() => {
    if (live) setPlaying(false);
  }, [live]);

  const buckets = data?.buckets || [];
  const bucketSpan = data?.bucket === "hour" ? HOUR : DAY;
  const maxima = useMemo(() => {
    const m = {};
    LANES.forEach((lane) => {
      m[lane.key] = Math.max(1, ...buckets.map((b) => b[lane.key] || 0));
    });
    return m;
  }, [buckets]);

  const pointerMs = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    return msAt(event.clientX - rect.left);
  };
  const onPointerDown = (event) => {
    if (event.clientX - event.currentTarget.getBoundingClientRect().left < LABEL_W) return;
    dragging.current = true;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setPlaying(false);
    setInstant(pointerMs(event));
  };
  const onPointerMove = (event) => {
    const ms = pointerMs(event);
    const bucket = buckets.find((b) => {
      const t0 = bucketMs(b.t);
      return ms >= t0 && ms < t0 + bucketSpan;
    });
    setHover(bucket ? { bucket, left: event.clientX - event.currentTarget.getBoundingClientRect().left } : null);
    if (dragging.current) setInstant(ms);
  };
  const stopDrag = () => {
    dragging.current = false;
  };

  const logged = Boolean(data?.collection_logged);
  const bucketMinutes = data?.bucket_minutes || 60;
  // Minutes a bucket could have been listened to (the current one is still running).
  const expected = (b) => {
    const t0 = bucketMs(b.t);
    return Math.max(1, Math.min(bucketMinutes, Math.floor((now - t0) / 60000)));
  };
  const top = logged ? LANE_H + LANE_GAP : 0;
  const height = top + LANES.length * (LANE_H + LANE_GAP) + AXIS_H;
  const ticks = useMemo(() => {
    const step = spanDays <= 1 ? 3 * HOUR : spanDays <= 7 ? DAY : spanDays <= 30 ? 5 * DAY : 15 * DAY;
    const first = Math.ceil(start / step) * step;
    const out = [];
    for (let t = first; t <= now; t += step) out.push(t);
    return out;
  }, [start, now, spanDays]);

  const step = (delta) => {
    setPlaying(false);
    setInstant(cursor + delta);
  };

  return (
    <div className="border-t border-tactical-line bg-tactical-panel/95 px-3 py-2 text-[11px] text-slate-400">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => {
            setPlaying(false);
            onChange(null);
          }}
          className={`flex items-center gap-1.5 border px-2 py-1 font-semibold tracking-wider ${
            live ? "border-tactical-green/60 bg-tactical-green/10 text-tactical-green" : "border-tactical-line text-slate-300 hover:text-white"
          }`}
          aria-pressed={live}
          title="Revenir à la situation en temps réel"
        >
          <Radio size={12} className={live ? "animate-pulse" : ""} /> DIRECT
        </button>
        <span
          className={`flex items-center gap-1.5 px-2 py-1 ${live ? "text-slate-300" : "border border-tactical-amber/60 bg-tactical-amber/10 font-semibold text-tactical-amber"}`}
        >
          {!live && <History size={12} />}
          {live ? "Situation en temps réel" : "REJEU"} · {formatInstant(cursor)}
        </span>
        <div className="flex items-center" role="group" aria-label="Déplacer le curseur temporel">
          <button type="button" onClick={() => step(-DAY)} className="px-1.5 py-1 hover:text-white" title="−1 jour" aria-label="Reculer d'un jour">
            <SkipBack size={13} />
          </button>
          <button type="button" onClick={() => step(-HOUR)} className="px-1.5 py-1 hover:text-white" title="−1 heure" aria-label="Reculer d'une heure">
            <StepBack size={13} />
          </button>
          <button
            type="button"
            onClick={() => {
              if (live) {
                setInstant(Date.now() - DAY); // replay the last 24 h
                setPlaying(true);
              } else setPlaying((p) => !p);
            }}
            className="px-1.5 py-1 text-tactical-cyan hover:text-white"
            title={playing ? "Pause" : live ? "Rejouer depuis 24 h" : "Lecture"}
            aria-label={playing ? "Pause" : "Lecture"}
          >
            {playing ? <Pause size={13} /> : <Play size={13} />}
          </button>
          <button type="button" onClick={() => step(HOUR)} disabled={live} className="px-1.5 py-1 hover:text-white disabled:opacity-30" title="+1 heure" aria-label="Avancer d'une heure">
            <StepForward size={13} />
          </button>
          <button type="button" onClick={() => step(DAY)} disabled={live} className="px-1.5 py-1 hover:text-white disabled:opacity-30" title="+1 jour" aria-label="Avancer d'un jour">
            <SkipForward size={13} />
          </button>
        </div>
        <select
          value={speed}
          onChange={(e) => setSpeed(Number(e.target.value))}
          className="border border-tactical-line bg-tactical-bg px-1.5 py-1 text-slate-300"
          aria-label="Vitesse de lecture"
        >
          {SPEEDS.map((s) => (
            <option key={s.ms} value={s.ms}>
              {s.label}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1">
          <span className="sr-only">Instant (UTC)</span>
          <input
            type="datetime-local"
            value={toInputValue(cursor)}
            min={toInputValue(oldest)}
            max={toInputValue(now)}
            onChange={(e) => e.target.value && setInstant(Date.parse(`${e.target.value}:00Z`))}
            className="border border-tactical-line bg-tactical-bg px-1.5 py-0.5 text-slate-300"
            title="Instant de référence, en UTC"
          />
          <span className="text-slate-500">UTC</span>
        </label>
        <div className="ml-auto flex border border-tactical-line" role="radiogroup" aria-label="Étendue de la frise">
          {SPANS.filter((s) => s.days <= retentionDays).map((s) => (
            <button
              key={s.days}
              type="button"
              role="radio"
              aria-checked={spanDays === s.days}
              onClick={() => onSpanChange(s.days)}
              className={`px-2 py-1 ${spanDays === s.days ? "bg-tactical-cyan/15 text-tactical-cyan" : "hover:text-white"}`}
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>

      <div ref={wrapRef} className="relative select-none">
        <svg
          width={width}
          height={height}
          className="block cursor-crosshair touch-none"
          role="img"
          aria-label={`Activité des ${spanDays} derniers jours par source ; curseur au ${formatInstant(cursor)}`}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={stopDrag}
          onPointerCancel={stopDrag}
          onPointerLeave={() => {
            stopDrag();
            setHover(null);
          }}
        >
          <defs>
            <pattern id="kraken-gap" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width="4" height="4" fill={COLLECTION.gap} opacity="0.18" />
              <line x1="0" y1="0" x2="0" y2="4" stroke={COLLECTION.gap} strokeWidth="1.5" />
            </pattern>
          </defs>
          {logged && (
            <g>
              <rect x={0} y={3} width={6} height={6} rx={1} fill={COLLECTION.color} />
              <text x={10} y={LANE_H - 2} fontSize="9.5" fill="#94a3b8">
                {COLLECTION.label}
              </text>
              {buckets.map((b) => {
                const t0 = bucketMs(b.t);
                if (t0 > now) return null;
                const x0 = Math.max(LABEL_W, x(t0));
                const w = Math.max(1, x(Math.min(t0 + bucketSpan, now)) - x0);
                const share = Math.min(1, (b[COLLECTION.key] || 0) / expected(b));
                if (share === 0) {
                  // Whole period without listening: hatched across every lane below.
                  return <rect key={b.t} x={x0} y={0} width={w} height={height - AXIS_H} fill="url(#kraken-gap)" />;
                }
                const h = Math.max(2, share * (LANE_H - 1));
                return <rect key={b.t} x={x0} y={LANE_H - h} width={w} height={h} fill={COLLECTION.color} opacity={0.4 + 0.6 * share} />;
              })}
            </g>
          )}
          {LANES.map((lane, i) => {
            const y = top + i * (LANE_H + LANE_GAP);
            return (
              <g key={lane.key}>
                <rect x={0} y={y + 3} width={6} height={6} rx={1} fill={lane.color} />
                <text x={10} y={y + LANE_H - 2} fontSize="9.5" fill="#94a3b8">
                  {lane.label}
                </text>
                <line x1={LABEL_W} x2={LABEL_W + plotW} y1={y + LANE_H} y2={y + LANE_H} stroke="#1c2a38" strokeWidth="1" />
                {buckets.map((b) => {
                  const value = b[lane.key] || 0;
                  if (!value) return null;
                  const t0 = bucketMs(b.t);
                  const x0 = Math.max(LABEL_W, x(t0));
                  const w = Math.max(1, x(t0 + bucketSpan) - x0 - (plotW / buckets.length > 4 ? 1 : 0));
                  const h = Math.max(2, (value / maxima[lane.key]) * (LANE_H - 1));
                  return <rect key={b.t} x={x0} y={y + LANE_H - h} width={w} height={h} rx={w > 4 ? 1 : 0} fill={lane.color} />;
                })}
              </g>
            );
          })}
          {ticks.map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={0} y2={height - AXIS_H} stroke="#1c2a38" strokeWidth="1" />
              <text x={x(t)} y={height - 3} fontSize="9" fill="#64748b" textAnchor="middle">
                {spanDays <= 1
                  ? new Date(t).toISOString().slice(11, 16)
                  : new Date(t).toISOString().slice(5, 10).split("-").reverse().join("/")}
              </text>
            </g>
          ))}
          <line
            x1={x(cursor)}
            x2={x(cursor)}
            y1={0}
            y2={height - AXIS_H}
            stroke={live ? "#3dff8a" : "#f5c542"}
            strokeWidth="2"
          />
          <polygon
            points={`${x(cursor) - 4},0 ${x(cursor) + 4},0 ${x(cursor)},5`}
            fill={live ? "#3dff8a" : "#f5c542"}
          />
        </svg>
        {hover && (
          <div
            className="pointer-events-none absolute bottom-full z-[1100] mb-1 w-48 border border-tactical-line bg-tactical-bg/95 p-2 text-[10px] shadow-xl"
            style={{ left: Math.min(Math.max(0, hover.left - 96), width - 192) }}
          >
            <p className="mb-1 text-slate-300">
              {hover.bucket.t.length === 13
                ? `${hover.bucket.t.replace("T", " ")}:00 Z (1 h)`
                : `${hover.bucket.t} (journée UTC)`}
            </p>
            {LANES.map((lane) => (
              <p key={lane.key} className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1 text-slate-400">
                  <span className="inline-block h-1.5 w-1.5" style={{ backgroundColor: lane.color }} />
                  {lane.label}
                </span>
                <span className="text-slate-200">{hover.bucket[lane.key] || 0}</span>
              </p>
            ))}
            {logged && (
              <p className="mt-1 flex items-center justify-between gap-2">
                <span className="text-slate-400">{COLLECTION.label}</span>
                <span className={(hover.bucket[COLLECTION.key] || 0) === 0 ? "text-tactical-red" : "text-slate-200"}>
                  {(hover.bucket[COLLECTION.key] || 0) === 0
                    ? "aucune écoute"
                    : `${Math.round(Math.min(1, hover.bucket[COLLECTION.key] / expected(hover.bucket)) * 100)} %`}
                </span>
              </p>
            )}
            {hover.bucket.incidents_closed > 0 && (
              <p className="mt-1 text-slate-500">Incidents clos : {hover.bucket.incidents_closed}</p>
            )}
          </div>
        )}
        {error && <p className="absolute inset-0 flex items-center justify-center text-slate-500">{error}</p>}
      </div>
    </div>
  );
}

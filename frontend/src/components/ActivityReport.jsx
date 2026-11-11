import { useEffect, useMemo, useState } from "react";
import { Anchor, BarChart3, ExternalLink, Ship } from "lucide-react";
import { errorMessage, fetchActivityReport } from "../services/api.js";
import { useReferenceTime } from "../lib/timeContext.js";
import { ACTIVITY, AIS_CATEGORIES, TARGET } from "../theme.js";
import DocLink from "./DocLink.jsx";
import VesselProfile from "./VesselProfile.jsx";

const PRESETS = [
  [30, "30 j"],
  [90, "3 mois"],
  [180, "6 mois"],
  [365, "12 mois"],
];
const STACK = ["foreign", "french", "unknown"]; // bottom → top: foreign activity sits on the baseline
const MAX_BARS = 45;
const sectionTitle = "mb-2 flex items-center gap-2 text-[10px] uppercase tracking-[0.25em] text-slate-500";

const day = (date) => date.toISOString().slice(0, 10);
const shift = (iso, days) => day(new Date(Date.parse(`${iso}T00:00:00Z`) + days * 86400000));

function hours(value) {
  if (value == null) return "—";
  return value >= 1000 ? `${(value / 1000).toFixed(1).replace(".", ",")} k h` : `${Math.round(value)} h`;
}

/** Daily buckets grouped by week (Monday) when the period has too many days for one bar each. */
function buckets(series) {
  if (series.length <= MAX_BARS || series[0]?.date?.length !== 10) return series.map((s) => ({ ...s, label: s.date }));
  const weeks = new Map();
  for (const s of series) {
    const d = new Date(`${s.date}T00:00:00Z`);
    const monday = day(new Date(d.getTime() - ((d.getUTCDay() + 6) % 7) * 86400000));
    const w = weeks.get(monday) || { date: monday, label: `sem. du ${monday}`, foreign: 0, french: 0, unknown: 0 };
    STACK.forEach((k) => (w[k] += s[k] || 0));
    weeks.set(monday, w);
  }
  return [...weeks.values()];
}

/** Stacked bars of fishing hours per day / week / month, foreign at the baseline, hover tooltip. */
function ActivityChart({ series }) {
  const [hover, setHover] = useState(null);
  const bars = useMemo(() => buckets(series), [series]);
  if (!bars.length) return <p className="text-[11px] text-slate-500">Aucune heure de pêche AIS sur la période.</p>;
  const W = 288;
  const H = 110;
  const max = Math.max(...bars.map((b) => STACK.reduce((t, k) => t + (b[k] || 0), 0)), 1);
  const slot = W / bars.length;
  const gap = slot >= 6 ? 2 : 1;
  const width = Math.max(1, slot - gap);
  const hovered = hover != null ? bars[hover] : null;
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H + 14}`} className="w-full" role="img" aria-label="Heures de pêche AIS par période, par pavillon">
        <line x1="0" x2={W} y1={H} y2={H} stroke="#1e293b" />
        <line x1="0" x2={W} y1="0.5" y2="0.5" stroke="#1e293b" strokeDasharray="2 3" />
        {bars.map((b, i) => {
          let y = H;
          return (
            <g key={b.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={i * slot} y="0" width={slot} height={H} fill="transparent" />
              {STACK.map((k) => {
                const h = ((b[k] || 0) / max) * (H - 2);
                if (h <= 0) return null;
                y -= h;
                const rect = (
                  <rect key={k} x={i * slot + gap / 2} y={y} width={width} height={Math.max(0, h - (y > 0 ? 1 : 0))}
                    rx={width >= 6 ? 2 : 0} fill={ACTIVITY[k].color} opacity={hover == null || hover === i ? 1 : 0.45} />
                );
                return rect;
              })}
            </g>
          );
        })}
        <text x="2" y="10" className="fill-slate-500 text-[9px]">{hours(max)}</text>
        <text x="0" y={H + 11} className="fill-slate-500 text-[9px]">{bars[0].date}</text>
        <text x={W} y={H + 11} textAnchor="end" className="fill-slate-500 text-[9px]">{bars[bars.length - 1].date}</text>
      </svg>
      {hovered && (
        <div className="pointer-events-none absolute left-0 right-0 top-3 mx-auto w-fit border border-tactical-line bg-tactical-bg/95 px-2 py-1 text-[10px] text-slate-200 shadow-lg">
          <p className="text-slate-400">{hovered.label}</p>
          {STACK.map((k) => (
            <p key={k} className="flex items-center gap-1.5">
              <span className="legend-dot" style={{ backgroundColor: ACTIVITY[k].color }} /> {ACTIVITY[k].label} : {hours(hovered[k])}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

function ShareRow({ label, value, share, color, hint }) {
  return (
    <li title={hint}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-slate-300">{label}</span>
        <span className="shrink-0 text-[10px] text-slate-400">
          {hours(value)} · {share.toLocaleString("fr-FR")} %
        </span>
      </div>
      <div className="mt-0.5 h-1 bg-white/5">
        <div className="h-1 rounded-r-sm" style={{ width: `${Math.max(1, share)}%`, backgroundColor: color }} />
      </div>
    </li>
  );
}

function Tile({ label, value, detail }) {
  return (
    <div className="border border-tactical-line px-2 py-1.5">
      <p className="text-[9px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className="text-sm text-white">{value}</p>
      {detail && <p className="text-[10px] text-slate-500">{detail}</p>}
    </div>
  );
}

function gfwMapUrl(report) {
  const { dataset, id } = report.region || {};
  if (!id) return null;
  const params = new URLSearchParams({ start: `${report.period.start}T00:00:00.000Z`, end: `${report.period.end}T00:00:00.000Z`, rC: "activity" });
  return `https://globalfishingwatch.org/map/fishing-activity/default-public/report/${dataset}/${id}?${params}`;
}

/**
 * EEZ fishing activity report over a chosen period, after the GFW map "area report":
 * hours by day and flag, flags, gear types, most active vessels (watchlist / licence), port visits.
 * The period ends at the reference day (replay included) unless dates are typed.
 */
export default function ActivityReport({ onShowEffort }) {
  const { at } = useReferenceTime();
  const refDay = at ? at.slice(0, 10) : day(new Date());
  const [preset, setPreset] = useState(90);
  const [custom, setCustom] = useState(null); // { start, end } typed by the operator
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [allVessels, setAllVessels] = useState(false);
  const [openMmsi, setOpenMmsi] = useState(null);

  const period = custom || { start: shift(refDay, -preset), end: refDay };

  useEffect(() => {
    if (!period.start || !period.end || period.start >= period.end) return undefined;
    let alive = true;
    setLoading(true);
    setError("");
    fetchActivityReport(period.start, period.end, at)
      .then((data) => alive && setReport(data))
      .catch((err) => alive && setError(errorMessage(err, "Rapport indisponible")))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period.start, period.end, at]);

  const vessels = report?.vessels || [];
  const link = report?.available ? gfwMapUrl(report) : null;

  return (
    <div className="space-y-5 p-4 text-xs">
      <section>
        <p className={sectionTitle}>
          <BarChart3 size={12} /> Activité de pêche dans la ZEE
          <DocLink page="situation/activite/" label="rapport d'activité" className="ml-auto" />
        </p>
        <div className="flex flex-wrap gap-1">
          {PRESETS.map(([days, label]) => (
            <button
              key={days}
              type="button"
              onClick={() => {
                setCustom(null);
                setPreset(days);
              }}
              className={`border px-2 py-0.5 text-[10px] ${!custom && preset === days ? "border-tactical-cyan text-tactical-cyan" : "border-tactical-line text-slate-400 hover:text-white"}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="mt-2 flex items-center gap-1 text-[10px] text-slate-400">
          <input type="date" value={period.start} max={period.end} onChange={(e) => setCustom({ ...period, start: e.target.value })}
            className="w-full border border-tactical-line bg-tactical-bg px-1 py-0.5 text-slate-200" aria-label="Début de la période" />
          →
          <input type="date" value={period.end} max={refDay} onChange={(e) => setCustom({ ...period, end: e.target.value })}
            className="w-full border border-tactical-line bg-tactical-bg px-1 py-0.5 text-slate-200" aria-label="Fin de la période" />
        </div>
        {loading && <p className="mt-2 text-[11px] text-slate-500">Interrogation de Global Fishing Watch (jusqu'à une minute)…</p>}
        {error && <p className="mt-2 text-[11px] text-tactical-red">{error}</p>}
        {report && !report.available && <p className="mt-2 text-[11px] text-tactical-amber">{report.reason}</p>}
      </section>

      {report?.available && (
        <>
          <section className="grid grid-cols-3 gap-1.5">
            <Tile label="Pêche AIS" value={hours(report.total_hours)} detail={`${report.period.days} j`} />
            <Tile label="Étrangère" value={`${report.foreign_share.toLocaleString("fr-FR")} %`} detail={hours(report.foreign_hours)} />
            <Tile label="Navires" value={report.vessel_count} detail={`${report.foreign_vessel_count} étrangers`} />
          </section>

          <section>
            <div className="mb-1 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-slate-400">
              {STACK.map((k) => (
                <span key={k} className="flex items-center gap-1">
                  <span className="legend-dot" style={{ backgroundColor: ACTIVITY[k].color }} /> {ACTIVITY[k].label}
                </span>
              ))}
            </div>
            <ActivityChart series={report.series} />
            {onShowEffort && (
              <button type="button" onClick={onShowEffort} className="mt-1 text-[10px] text-tactical-cyan hover:underline">
                Afficher l'effort de pêche sur la carte
              </button>
            )}
          </section>

          <section>
            <p className={sectionTitle}>Pavillons</p>
            <ul className="space-y-1.5">
              {report.by_flag.slice(0, 8).map((f) => (
                <ShareRow key={f.code || "?"} label={f.flag} value={f.hours} share={f.share}
                  color={f.code ? (f.foreign ? ACTIVITY.foreign.color : ACTIVITY.french.color) : ACTIVITY.unknown.color} />
              ))}
            </ul>
          </section>

          {report.by_gear.length > 0 && (
            <section>
              <p className={sectionTitle}>Engins (GFW)</p>
              <ul className="space-y-1.5">
                {report.by_gear.slice(0, 6).map((g) => (
                  <ShareRow key={g.gear} label={g.label} value={g.hours} share={g.share} color="#64748b" />
                ))}
              </ul>
            </section>
          )}

          <section>
            <p className={sectionTitle}>
              <Ship size={12} /> Navires les plus actifs
            </p>
            <ul className="divide-y divide-tactical-line border-y border-tactical-line">
              {vessels.slice(0, allVessels ? vessels.length : 10).map((v) => {
                const key = v.gfw_id || v.mmsi || v.name;
                const open = openMmsi && openMmsi === v.mmsi;
                return (
                  <li key={key} className="py-1.5">
                    <button type="button" disabled={!v.mmsi} onClick={() => setOpenMmsi(open ? null : v.mmsi)}
                      className="flex w-full items-start justify-between gap-2 text-left enabled:hover:text-tactical-cyan"
                      title={v.mmsi ? "Fiche fusion du navire" : "MMSI inconnu de GFW"}>
                      <span className="min-w-0">
                        <span className="block truncate text-slate-200">{v.name || "Nom inconnu"}</span>
                        <span className="block truncate text-[10px] text-slate-500">
                          {v.flag || "pavillon ?"} · {v.gear || "engin ?"}{v.mmsi ? ` · ${v.mmsi}` : ""}
                        </span>
                      </span>
                      <span className="shrink-0 text-right text-[10px]">
                        <span className="block text-slate-300">{hours(v.hours)}</span>
                        {v.watchlist && <span className="block" style={{ color: TARGET.listed.color }}>⚠ liste {v.watchlist.source}</span>}
                        {!v.watchlist && v.licence && <span className="block" style={{ color: AIS_CATEGORIES.licensed.color }}>licence</span>}
                        {!v.watchlist && !v.licence && v.foreign && <span className="block" style={{ color: ACTIVITY.foreign.color }}>étranger</span>}
                      </span>
                    </button>
                    {open && (
                      <div className="mt-1 border-l border-tactical-cyan/40 pl-2">
                        <VesselProfile mmsi={v.mmsi} />
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
            {vessels.length > 10 && (
              <button type="button" onClick={() => setAllVessels(!allVessels)} className="mt-1 text-[10px] text-tactical-cyan hover:underline">
                {allVessels ? "Réduire" : `Voir les ${vessels.length} navires`}
              </button>
            )}
          </section>

          {report.ports && (
            <section>
              <p className={sectionTitle}>
                <Anchor size={12} /> Escales dans les ports de la zone
              </p>
              {report.ports.length === 0 ? (
                <p className="text-[11px] text-slate-500">Aucune escale détectée par GFW sur la période.</p>
              ) : (
                <ul className="space-y-1">
                  {report.ports.slice(0, 8).map((p) => (
                    <li key={p.port} className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-slate-300">{p.port}</span>
                      <span className="shrink-0 text-[10px] text-slate-400">
                        {p.visits} escale{p.visits > 1 ? "s" : ""} · {p.vessels} navire{p.vessels > 1 ? "s" : ""}
                        {p.foreign_visits > 0 && <span style={{ color: ACTIVITY.foreign.color }}> · {p.foreign_visits} étr.</span>}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          <p className="text-[10px] text-slate-600">
            {report.source} · heures de pêche apparente déduites de l'AIS : un navire qui coupe son AIS n'y figure pas (voir détections SAR).
            {link && (
              <>
                {" "}
                <a href={link} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 text-tactical-cyan hover:underline">
                  Ouvrir sur la carte GFW <ExternalLink size={10} />
                </a>
              </>
            )}
          </p>
        </>
      )}
    </div>
  );
}

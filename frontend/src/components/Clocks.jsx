import { useEffect, useState } from "react";

// Operational clocks: maritime ops run on UTC (Zulu); Paris for the national
// chain of command (CET/CEST, offset computed so it stays right after DST);
// Cayenne local time (UTC-3, no DST).
const ZONES = [
  { id: "UTC", label: "UTC · Zulu", tz: "UTC" },
  { id: "PAR", label: "Métropole", tz: "Europe/Paris" },
  { id: "CAY", label: "Cayenne", tz: "America/Cayenne" },
];

function offsetLabel(tz, date) {
  if (tz === "UTC") return "UTC+0";
  const part = new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "shortOffset" })
    .formatToParts(date)
    .find((p) => p.type === "timeZoneName");
  return (part?.value || "").replace("GMT", "UTC") || tz;
}

export default function Clocks() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <div className="hidden shrink-0 items-center divide-x divide-tactical-line border border-tactical-line xl:flex" aria-label="Horloges">
      {ZONES.map((zone) => (
        <div key={zone.id} className="px-2.5 py-1 text-center leading-tight">
          <p className="font-mono text-sm tabular-nums text-white">
            {new Intl.DateTimeFormat("fr-FR", { timeZone: zone.tz, hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(now)}
          </p>
          <p className="text-[9px] uppercase tracking-wider text-slate-500">
            {zone.label} <span className="text-slate-600">{offsetLabel(zone.tz, now)}</span>
          </p>
        </div>
      ))}
    </div>
  );
}

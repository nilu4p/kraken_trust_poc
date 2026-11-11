import { RISK, riskKey } from "../theme.js";

export default function RiskBadge({ score, level, compact = false }) {
  const key = riskKey(level, score);
  if (score == null) {
    return compact ? null : <span className="text-slate-500">Risque non évalué</span>;
  }
  const { color, label } = RISK[key];
  return (
    <span
      className="inline-flex items-center gap-1 border px-2 py-0.5 text-[10px] uppercase tracking-wider"
      style={{ color, borderColor: `${color}99`, backgroundColor: `${color}1a` }}
    >
      {compact ? score : `Risque ${label} · ${score}/100`}
    </span>
  );
}

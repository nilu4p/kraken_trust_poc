import { ShieldAlert, ShieldCheck, ShieldQuestion } from "lucide-react";
import { C2PA } from "../theme.js";
import DocLink from "./DocLink.jsx";

const ICONS = { verified: ShieldCheck, fallback: ShieldQuestion, unverifiable: ShieldQuestion, tampered: ShieldAlert };

export default function C2PABadge({ status }) {
  const key = C2PA[status] ? status : "tampered";
  const { color, label } = C2PA[key];
  const Icon = ICONS[key];
  return (
    <div
      className="flex items-center gap-2 border px-3 py-2 text-xs tracking-wide"
      style={{ color, borderColor: `${color}80`, backgroundColor: `${color}1a` }}
    >
      <Icon size={14} />
      <span>{label}</span>
      <DocLink page="preuves/" label="scellement C2PA et vérification des preuves" className="ml-auto" />
    </div>
  );
}

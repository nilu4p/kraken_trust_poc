import { CircleHelp } from "lucide-react";

// Knowledge base served by the "docs" service behind the same nginx (/docs/).
export const DOCS_URL = "/docs/";

/** Contextual help: opens the matching documentation page in a new tab. */
export default function DocLink({ page = "", label = "Aide", className = "" }) {
  return (
    <a
      href={`${DOCS_URL}${page}`}
      target="_blank"
      rel="noopener"
      title={`Documentation : ${label}`}
      aria-label={`Documentation : ${label}`}
      className={`inline-flex items-center gap-1 text-slate-500 hover:text-tactical-cyan ${className}`}
    >
      <CircleHelp size={13} />
    </a>
  );
}

import { useEffect, useState } from "react";
import { errorMessage, fetchVesselProfile } from "../services/api.js";
import { formatInstant, useReferenceTime } from "../lib/timeContext.js";
import { FEEDS, TARGET } from "../theme.js";

function formatDate(value) {
  return value ? String(value).slice(0, 16).replace("T", " ") : "—";
}

/**
 * Fused vessel profile shown in the AIS popup: feeds that hear the vessel,
 * VMS licence, registry record (VesselAPI), GFW Insights indicators, sanctions matches and linked incidents.
 * Mounted only while the popup is open (one request per opening).
 */
export default function VesselProfile({ mmsi }) {
  const { at } = useReferenceTime();
  const [profile, setProfile] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    setProfile(null);
    setError("");
    fetchVesselProfile(mmsi, at)
      .then((data) => alive && setProfile(data))
      .catch((err) => alive && setError(errorMessage(err)));
    return () => {
      alive = false;
    };
  }, [mmsi, at]);

  if (error) return <p className="mt-1 text-[11px] text-slate-500">Fiche fusion indisponible : {error}</p>;
  if (!profile) return <p className="mt-1 text-[11px] text-slate-500">Fusion des sources…</p>;

  const gfw = profile.intel?.gfw;
  const sanctions = profile.intel?.sanctions || [];
  const registry = profile.intel?.registry;
  const registryLine = registry
    ? [
        [registry.vessel_type, registry.vessel_subtype].filter(Boolean).join(" / "),
        registry.flag && `pavillon ${registry.flag}`,
        registry.year_built && `construit en ${registry.year_built}`,
        registry.length_m && `${registry.length_m} m`,
        registry.gross_tonnage && `${registry.gross_tonnage} GT`,
        registry.home_port && `port d'attache ${registry.home_port}`,
      ].filter(Boolean)
    : [];
  const feeds = Object.entries(profile.feeds || {});
  const indicators = gfw
    ? [
        gfw.iuu_listed && `inscrit INN ORGP (${gfw.iuu_times_listed || 1}×)`,
        gfw.fishing_in_no_take_mpa > 0 && `${gfw.fishing_in_no_take_mpa} pêche(s) en AMP intégrale`,
        gfw.fishing_without_rfmo_authorization > 0 && `${gfw.fishing_without_rfmo_authorization} pêche(s) sans autorisation ORGP`,
        gfw.ais_off_events > 0 && `${gfw.ais_off_events} coupure(s) AIS`,
        gfw.flag_changes > 0 && `${gfw.flag_changes} changement(s) de pavillon`,
      ].filter(Boolean)
    : [];

  return (
    <div className="mt-2 space-y-1 border-t border-slate-600/40 pt-2 text-[11px]">
      {at && (
        <p className="text-tactical-amber">
          Fiche au {formatInstant(at)} : sources et trace des 24 h précédentes, incidents au statut de cet instant.
          {profile.intel_note && <span className="block text-slate-500">{profile.intel_note}.</span>}
        </p>
      )}
      <p>
        <strong>Sources (24 h) :</strong>{" "}
        {feeds.length ? feeds.map(([feed, info]) => `${FEEDS[feed]?.label || feed} ${info.positions_24h}`).join(" · ") : "—"}
        {profile.licensed_vms && <span style={{ color: "#3dff8a" }}> · sous licence (VMS)</span>}
      </p>
      {profile.licence && profile.licence.licence !== "VMS" && (
        <p style={{ color: "#5eead4" }}>
          <strong>Navire autorisé :</strong> licence {profile.licence.licence || "—"}
          {profile.licence.valid_until ? ` · valide jusqu'au ${profile.licence.valid_until}` : ""}
        </p>
      )}
      {profile.vessel.imo && (
        <p>
          IMO {profile.vessel.imo}
          {profile.vessel.call_sign ? ` · indicatif ${profile.vessel.call_sign}` : ""}
        </p>
      )}
      {registry && (
        <p>
          <strong>Registre (VesselAPI) :</strong> {registryLine.length ? registryLine.join(" · ") : "navire inconnu du registre"}
          {registry.imo && !profile.vessel.imo ? ` · IMO ${registry.imo}` : ""}
          {registry.former_names?.length > 0 && (
            <span style={{ color: "#fbbf24" }}>
              {" "}
              · ex-{registry.former_names.map((f) => f.name + (f.until ? ` (→${f.until})` : "")).join(", ")}
            </span>
          )}
        </p>
      )}
      {gfw && (
        <p>
          <strong>GFW Insights (12 mois) :</strong> {indicators.length ? indicators.join(", ") : "aucun indicateur de risque"}
          {gfw.ais_coverage_pct != null && ` · couverture AIS ${Math.round(gfw.ais_coverage_pct)} %`}
        </p>
      )}
      {sanctions.map((match) => (
        <p key={match.id || match.caption} style={{ color: TARGET.listed.color }}>
          <strong>⚠ Sanctions :</strong> {match.caption} (score {Number(match.score).toFixed(2)}) — {(match.datasets || []).slice(0, 3).join(", ")}
          {match.url && (
            <>
              {" "}
              <a href={match.url} target="_blank" rel="noreferrer" className="underline">
                fiche ↗
              </a>
            </>
          )}
        </p>
      ))}
      {!gfw && !sanctions.length && !registry && <p className="text-slate-500">Renseignement navire : pas encore criblé (GFW / OpenSanctions).</p>}
      {profile.incidents?.length > 0 && (
        <p>
          <strong>Incidents :</strong>{" "}
          {profile.incidents.map((i) => (
            <a key={i.id} href={`#/incidents/${i.id}`} className="mr-1 underline">
              {i.ref}
            </a>
          ))}
        </p>
      )}
      <p className="text-slate-500">Dernière position {formatDate(profile.vessel.last_seen)} Z via {FEEDS[profile.vessel.last_source]?.label || profile.vessel.last_source}</p>
      <p>
        <a href={`#/graph/${encodeURIComponent(`vessel:${mmsi}`)}`} className="text-tactical-cyan underline">
          Explorer les liens de ce navire (graphe) →
        </a>
      </p>
    </div>
  );
}

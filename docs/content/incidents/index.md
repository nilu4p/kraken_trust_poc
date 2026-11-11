# File d'incidents

<p class="where">Onglet <strong>Incidents</strong>. Le dossier d'un incident s'ouvre à droite de la liste.</p>

Un **incident** est une anomalie à traiter : un navire sans AIS détecté au radar, ou un comportement suspect repéré à l'AIS par une règle 24/7. Les détections proches et répétées d'un même objet sont **regroupées** dans un seul incident (voir [corrélation](dossier.md#correlation-des-detections)) pour ne pas noyer la file.

## Filtres

| Filtre | Contenu |
| --- | --- |
| **Ouverts** | Tous les incidents non clos (par défaut) |
| **Mes incidents** | Ceux qui vous sont affectés |
| **Non assignés** | Ouverts sans opérateur : à prendre en priorité |
| **Tous** | Y compris les incidents clos |
| **Toutes sévérités / P1…P4** | Restreint à une sévérité |
| **Réf. ou titre** | Recherche texte (ex. `KRK-2026-0042`, `Brésil`, nom de navire) |
| **Ordre** | *Plus récents d'abord* (défaut), *Plus anciens d'abord* (traiter le stock dans l'ordre d'arrivée) ou *Par priorité* (non clos d'abord, puis sévérité) ; le choix est mémorisé par le navigateur |

Filtre par défaut : **Ouverts** pour les analystes et au-delà, **Tous** pour les observateurs. Le bouton **?** ouvre cette documentation.

## Colonnes

| Colonne | Signification |
| --- | --- |
| **Sév.** | Sévérité P1 (critique) à P4 (faible) — voir [Sévérités](severite-sla.md) |
| **Ouvert le** | Date et heure (UTC) d'ouverture de l'incident : la clé de l'ordre chronologique |
| **Incident** | Référence `KRK-année-numéro`, titre, type |
| **Statut** | Étape du [cycle de vie](cycle-de-vie.md), avec sa couleur |
| **Risque** | Score de risque /100 le plus élevé observé |
| **SLA** | Temps restant avant la prochaine échéance (triage tant que l'incident n'est pas pris en charge, puis résolution) ; **dépassé** en rouge ; *clos* ou *clos hors SLA* une fois fermé |
| **Assigné** | Opérateur responsable, ou *Non assigné* |

Ordre : **chronologique selon la date d'ouverture**, du plus récent au plus ancien par défaut (filtre *Ordre* pour l'inverse ou pour un tri par priorité).

## Couleurs des statuts

| Statut | Couleur |
| --- | --- |
| Nouveau | <span class="sw" style="--c:#ff3b3b"></span> rouge |
| En triage | <span class="sw" style="--c:#f5c542"></span> jaune |
| Qualifié | <span class="sw" style="--c:#38bdf8"></span> bleu |
| Escaladé | <span class="sw" style="--c:#a78bfa"></span> violet |
| Intervention en cours | <span class="sw" style="--c:#00e5ff"></span> cyan |
| Clos | <span class="sw" style="--c:#64748b"></span> gris |

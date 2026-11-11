# Fusion multi-sources

Aucune source ne suffit seule : l'AIS peut être coupé, le radar ne passe que quelques fois par semaine, le VMS ne concerne que les navires sous licence. La fusion consiste à **croiser** les sources pour qu'une absence dans l'une soit comblée ou rendue suspecte par l'autre.

## 1. Fusion des positions

| Flux | Nature | Fréquence |
| --- | --- | --- |
| aisstream.io | AIS terrestre, flux continu | temps réel |
| AISHub | AIS terrestre d'autres stations | toutes les 120 s (`aishub_poll_seconds`) |
| VesselAPI (facultatif) | AIS agrégé du fournisseur | toutes les `vesselapi_poll_minutes` (0 = désactivé, défaut) |
| VMS | balises des navires sous licence | à chaque import |

- Toutes les positions vont dans **un historique unique** ; chacune garde sa **source**.
- Une même position reçue par deux flux (même navire, même horodatage) n'est stockée **qu'une fois**.
- Un flux en retard ne peut pas « reculer » un navire : seule une position **plus récente** met à jour sa dernière position.
- La corrélation radar indique quels flux ont servi (« AISHub + aisstream.io (historique) »).

## 2. Enrichissement des navires (renseignement)

Pour chaque navire entendu dans la ZEE dans l'heure, au plus `intel_lookups_per_tick` (10) par minute, revérifié tous les `intel_refresh_days` (7) jours :

| Source | Ce qui est ajouté |
| --- | --- |
| GFW identité | pavillon d'immatriculation, engins de pêche, identifiant GFW |
| GFW Insights (12 mois) | inscription INN d'ORGP, pêches en AMP intégrale, pêches sans autorisation ORGP, coupures AIS, changements de pavillon, couverture AIS |
| VesselAPI (registre) | IMO, indicatif, type et sous-type, année, dimensions, tonnage, port d'attache, anciens noms — navires étrangers de pêche ou de type non déclaré ; un IMO ou indicatif manquant est complété, ce qui fiabilise le criblage OpenSanctions qui suit |
| OpenSanctions | correspondances avec les listes de sanctions (score ≥ seuil) |

Les autres navires (marine marchande en transit) sont décrits par VesselAPI **à l'ouverture de leur fiche**. Chaque fiche registre est conservée 30 jours.

!!! warning "Quota VesselAPI"
    Le fournisseur compte chaque appel (offre gratuite : 150 par mois). La plateforme tient un compteur mensuel (`vesselapi_monthly_calls`), se cale sur le quota restant annoncé par VesselAPI et s'arrête d'elle-même au budget. Le flux de positions n'utilise jamais les 30 derniers pourcents du budget, réservés aux fiches registre. Consommation visible dans *Couverture → Renseignement navire*.

Le résultat est stocké sur le navire, alimente les règles *liste INN* et *sanctions*, et s'affiche dans la [fiche navire](../situation/fiche-navire.md).

## 3. Contexte des cibles radar

| Source | Effet |
| --- | --- |
| NOAA ETOPO (bathymétrie) | fiabilité : terre émergée −0,40, haut-fond −0,20 |
| Open-Meteo (état de mer) | fiabilité −0,05 à −0,15 |
| GFW SAR (4Wings) | fiabilité +0,15 si confirmé le jour même ; risque +3 par détection sans AIS proche |
| GFW événements | risque : coupures, rencontres, flânages, pêches à proximité |
| Copernicus Marine | risque : front thermique +5, chlorophylle ≥ 1 mg/m³ +5 ; courant affiché |
| WDPA | risque : +10 dans une aire protégée |
| SHOM | risque : +5 près de la limite des 3 NM |
| Liste de surveillance | risque : +30 |

## 4. Évolutions possibles

Sources recensées mais non branchées, et pourquoi, dans [Sources](../sources.md#sources-non-branchees).

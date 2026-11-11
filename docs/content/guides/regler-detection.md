# Régler la détection

Réservé aux administrateurs (*Administration → Paramètres*). Changez **un paramètre à la fois** et observez une à deux semaines.

## Diagnostic

| Symptôme | Indice | Réglage |
| --- | --- | --- |
| Beaucoup de fausses alarmes radar | part de *Fausse alarme* dans les clôtures > 30 % | `min_detection_quality` 0,25 → 0,35–0,40 ; ou `cfar_k` 5 → 6 |
| Fausses alarmes par mer forte | concentrées les jours de vent | `cfar_k` 6 temporairement |
| Petites embarcations manquées | pirogues vues par d'autres moyens mais pas détectées | `cfar_k` 5 → 4 par mer calme ; `min_detection_quality` 0,2 |
| Même navire = plusieurs incidents | doublons fréquents | `incident_correlation_km` 15 → 25 ; `incident_correlation_hours` 72 → 96 |
| Navires différents fusionnés | incidents mélangeant deux cibles | `incident_correlation_km` → 8–10 |
| Échos AIS non appariés (navires rapides) | navires AIS à côté des cibles « sans AIS » | `ais_correlation_window_h` 3 → 2 (positions plus proches du passage) |
| Trop d'alertes de coupure AIS | coupures dues à la couverture radio | `ais_gap_hours` 4 → 6 |
| Rendez-vous trop nombreux | navires côte à côte hors des mouillages connus | `encounter_distance_km` 0,5 → 0,3 ; `encounter_min_minutes` 60 → 120 |
| Homonymes en sanctions | correspondances par nom seul | `sanctions_min_score` 0,75 → 0,9 |

## Apprentissage

La plateforme apprend aussi des opérateurs : une **Fausse alarme** classée retire 0,45 de fiabilité aux détections futures à moins de 250 m. Classer correctement est donc le premier réglage.

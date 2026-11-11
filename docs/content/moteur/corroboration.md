# Corroboration multi-capteurs

<p class="where">Dans chaque incident : bloc <strong>Corroboration multi-capteurs</strong> de l'aide à la décision, et section du même nom dans le compte rendu d'incident.</p>

Voir aussi : [Aide à la décision](../commandement/decision.md) · [Rapports](../commandement/rapports.md).

Un écho radar seul peut être un artefact. La corroboration répond à une question : **un autre capteur, indépendant du radar, a-t-il vu un navire au même endroit et au même moment ?** Si oui, la cible est confirmée, même si l'écho radar était faible. Chaque capteur a ses angles morts : l'optique ne voit rien sous les nuages ni la nuit, les lumières nocturnes ne montrent que les navires éclairés, et l'AIS que ceux qui veulent être vus.

## Capteurs utilisés

| Code | Capteur | Source | Précision de position | Poids |
| --- | --- | --- | --- | --- |
| `SAR` | Imagerie radar | Sentinel-1 : cibles scellées sans AIS | 100 m | [indice de fiabilité](fiabilite.md) de la détection |
| `EO` | Imagerie optique | [Sentinel-2 10 m, Landsat 8/9 30 m](detection-multicapteur.md#optique) | 50 à 100 m | confiance du détecteur, réduite s'il y a des nuages autour |
| `IR` | Infrarouge thermique | Landsat TIRS : cible plus chaude que la mer d'au moins 0,5 K | 100 m | 60 % |
| `NIGHT` | Lumières nocturnes | [VIIRS jour/nuit, 3 satellites](detection-multicapteur.md#lumieres-nocturnes-viirs) | 800 m, ± 1 h | confiance du détecteur |
| `GFW` | Radar Global Fishing Watch | détections Sentinel-1 publiées chaque jour par GFW | 500 m, ± 12 h | 75 % |
| `AIS` / `VMS` | Coopératif | positions déclarées par le navire, balises des navires sous licence | 100 m | 99 % (identité déclarée) |

Les détections optiques et nocturnes proviennent de la [veille automatique](detection-multicapteur.md#veille-automatique) (chaque heure) et du bouton **Analyser l'imagerie optique / thermique autour** de l'aide à la décision.

## Comment les observations sont rapprochées

1. **Navires AIS.** Les positions AIS / VMS de chaque MMSI forment une piste. Une observation d'un autre capteur est **expliquée** par un navire AIS si la position de ce navire à cet instant tombe dans la porte d'association. Cette position est interpolée entre deux positions, ou extrapolée à cap et vitesse constants jusqu'à 3 h. La porte vaut 3 × (incertitudes des deux positions) + 0,3 km.
2. **Navires sans AIS.** Les observations restantes sont enchaînées dans l'ordre du temps. Une observation rejoint le contact dont la dernière observation pouvait être atteinte à la **vitesse maximale d'association** ([`fusion_gate_speed_kn`](../administration/parametres.md#fusion-gate-speed-kn), 15 nœuds par défaut), dans un délai de 24 h au plus. On ajoute les incertitudes de position et d'heure. Le contact le plus proche l'emporte.
3. **Jamais rapprochées.** Deux détections de la **même acquisition** (même image, même nuit, même scène radar) sont toujours deux navires distincts.
4. **Une piste n'est pas une preuve.** Relier deux observations par une vitesse plausible donne seulement une *piste*. Pour dire que deux capteurs ont vu **le même navire**, il faut qu'ils l'aient vu dans la même zone : à moins de 12 h d'écart, et à une distance inférieure à (incertitudes + 5 km + 3 km/h × écart), soit le rythme d'un navire en pêche. Un écho radar et une lumière à 40 km et 6 h d'intervalle restent une piste « même navire possible, non confirmé ».

## Confiance

Pour chaque capteur, on garde sa meilleure observation, car plusieurs vues du même capteur ne sont pas indépendantes. Les capteurs **qui se corroborent** (voir ci-dessus) se combinent comme des preuves indépendantes. Un capteur relié seulement par une piste lâche n'ajoute rien :

**P = 1 − Π (1 − p<sub>capteur</sub>)**

Exemple : une lumière VIIRS à 60 % et un navire vu par Sentinel-2 à 70 % donnent 1 − 0,4 × 0,3 = **88 %**.

| Statut | Signification | Couleur |
| --- | --- | --- |
| **Sans AIS — corroboré** | aucune émission AIS / VMS, et au moins deux capteurs indépendants qui se corroborent | <span class="sw" style="--c:#ff3b3b"></span> rouge |
| **Sans AIS — à confirmer** | un seul capteur, ou plusieurs reliés seulement par une piste : à vérifier (prochain passage, survol, patrouille) | <span class="sw" style="--c:#f5c542"></span> jaune |
| **Identifié** | navire AIS / VMS également vu par un capteur | <span class="sw" style="--c:#3dff8a"></span> vert |

## Effet sur l'incident

L'aide à la décision cherche, sur les 36 h précédant la dernière observation, le contact qui porte l'incident. À défaut, elle prend le plus proche à moins de 3 km. Elle affiche ses capteurs et les autres navires vus à moins de 25 km.

- Un écho radar **peu fiable** (indice < 45 %) mais **corroboré**, avec une confiance d'au moins 80 %, n'est plus considéré comme douteux. L'interception peut alors être recommandée sans levée de doute préalable.
- Le compte rendu d'incident contient une section **Corroboration multi-capteurs** : chaque observation, avec sa source et son poids.
- Le bouton **Analyser l'imagerie optique / thermique autour** lit les images Sentinel-2 et Landsat des 5 derniers jours dans le rayon d'incertitude de la cible. Il montre les vignettes et garde les détections pour la corroboration (rôle analyste).

## Limites

- Les observations sont rapprochées par **proximité dans l'espace et le temps**. Deux navires sans AIS qui naviguent ensemble peuvent être confondus. Un navire qui dépasse la vitesse d'association peut être compté deux fois.
- **Premier essai sur données réelles** (octobre 2026, 7 jours : 5 cibles radar, 19 lumières VIIRS, 2 scènes optiques sans navire) : 15 contacts, tous « à confirmer ». Les passages se font à des heures très différentes : Sentinel-1 vers 21:30 UTC, VIIRS vers 04:30 UTC, Sentinel-2 vers 14:00 UTC. La corroboration vient donc surtout des navires qui restent sur zone (en pêche).
- Les heures VIIRS sont **nominales** (± 1 h) et les détections GFW **quotidiennes** (± 12 h) : leur rapprochement reste large.
- Rien n'est enregistré par la corroboration : elle est recalculée à chaque ouverture de l'aide à la décision. Ce qui fait foi est le compte rendu émis, qui la fige.

Détail des détecteurs : [Détection multi-capteurs](detection-multicapteur.md).

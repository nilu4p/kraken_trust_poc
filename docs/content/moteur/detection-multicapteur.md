# Détection multi-capteurs

<p class="where">Automatique (veille horaire, <code>sensor_watch_enabled</code>) ; à la demande avec le bouton <strong>Analyser l'imagerie optique / thermique autour</strong> de l'aide à la décision d'un incident. Chaque scène analysée est tracée dans l'audit (<code>sensor.scan</code>, <code>sensor.optical_scan</code>).</p>

Les détections servent à la [corroboration des cibles radar](corroboration.md).

| Capteur | Résolution | Quand | Ce qu'il voit | Archive (sans clé) |
| --- | --- | --- | --- | --- |
| Sentinel-1 (radar) | 10 m (pixel), ~20 m (résolution) | jour et nuit, tous temps, ~21:30 et ~09:30 UTC, une même zone vue tous les 6 à 12 jours | coques métalliques ; bois et fibre moins bien | Copernicus / Planetary Computer |
| **Sentinel-2 MSI** | 10 m | ~14:00 UTC, tous les 2-5 jours, **eaux côtières** (~20 km) | coques, ponts, sillages | Earth Search (AWS) |
| **Landsat 8/9 OLI-TIRS** | 30 m + thermique 100 m | ~13:30 UTC, tous les 8 jours (2 satellites) | coques, anomalies thermiques des grandes unités | Planetary Computer |
| **VIIRS jour/nuit** (SNPP, NOAA-20, NOAA-21) | ~750 m | chaque nuit, ~01:30 heure locale (~04:30 UTC), toute la ZEE | navires éclairés | NASA GIBS |

## Radar : artefacts écartés

Deux sources classiques de faux échos sur Sentinel-1 sont maintenant éliminées, à l'échelle de la **scène entière** (le fantôme et son navire tombent souvent dans des tuiles différentes) :

| Artefact | Signature | Règle |
| --- | --- | --- |
| **Ambiguïté azimutale** | réplique d'un écho brillant décalée de 3,5 à 7,5 km dans le sens du vol (Δx = λ·R·PRF / 2V), à la même distance radar, 20 à 30 dB plus faible | écho ≥ 13 dB plus faible qu'un autre, à ± 6 px en colonne et à 3,5-7,5 km en ligne |
| **Lobe secondaire** | croix autour d'une très grosse coque | écho ≥ 15 dB plus faible, aligné (± 2 px) à moins de 400 m |

Les plus brillants sont examinés d'abord et ne sont jamais écartés par un plus faible ; un petit navire réel à côté d'un grand (autre colonne, ou contraste < 13 dB) est conservé. Le bilan du scan indique le nombre d'artefacts écartés.

## Optique

Principe : l'eau absorbe le proche infrarouge, une coque le réfléchit. Détection CFAR locale sur la réflectance proche infrarouge des **pixels d'eau sans nuage**, puis filtres physiques :

| Étape | Règle | Écarte |
| --- | --- | --- |
| Masque | classification de scène (Sentinel-2 SCL) ou bande qualité (Landsat QA_PIXEL) ; marge de **150 m** autour des nuages, ombres et bords sans données | nuages, brume de bord, côtes |
| Coques étiquetées « nuage » | un « nuage » plus petit qu'un navire (≤ 400 m) reste candidat | — (récupère les coques blanches classées nuage) |
| Contraste | ≥ `optical_cfar_k` écarts-types (6) **et** ≥ 0,02 de réflectance au-dessus de l'eau locale | texture des eaux turbides |
| Taille / forme | 2 px à 400 m ; objets filiformes > 150 m d'allongement ≥ 8 écartés | jetées, conduites, sillages seuls |
| **SWIR** (1,6 µm) | la cible doit réfléchir l'infrarouge à ondes courtes (≥ +0,012) : l'eau, même chargée de vase, et l'écume l'absorbent | fronts de turbidité du panache amazonien, écume |
| **Ombre** | une zone sombre (verte, 4 écarts robustes sous l'eau voisine) aussi grande que l'objet à moins de 600 m = un cumulus et son ombre | petits cumulus non masqués |
| **Thermique** (Landsat) | cible plus froide que la mer de plus de 1,5 K = sommet de nuage | nuages |
| Côte (Guyane) | à terre, à moins de 60 m du rivage ou à 80 m d'une structure OSM (quai, digue, balise) | quais, épis, balises |
| Doublons | même passage, à moins de la demi-longueur (60 m min.) : tuiles Sentinel-2 qui se recouvrent, fragments d'une grande coque | doublons |

**Confiance** : 0,5 à 0,95 selon le contraste, −0,1 pour une cible de 2 pixels, puis × (1 − 0,6 × part de nuages autour). Une cible optique isolée en zone nuageuse pèse donc peu tant qu'aucune autre discipline ne la confirme.

!!! info "Validation sur images réelles (octobre 2026)"
    - **Mouillage de Gibraltar** (contrôle positif, ciel clair) : plus de 100 navires détectés sur deux tuiles Sentinel-2 et 14 sur Landsat. Restent quelques cumulus et des jetées (hors de Guyane, le trait de côte OSM n'est pas chargé).
    - **Approches de Cayenne** (eau très turbide, cumulus) : les faux positifs passent de 42 à 9 avec les filtres SWIR, ombre et marge nuageuse. Les cibles restantes en scène nuageuse ont une confiance réduite.

    Le résidu (petits cumulus diffus) est la limite connue de l'optique à 10-30 m : c'est la [corroboration](corroboration.md) avec le radar et les lumières nocturnes qui tranche.

## Lumières nocturnes (VIIRS)

La bande jour/nuit des trois satellites VIIRS voit, chaque nuit, les navires éclairés (pêche au lamparo, éclairage de pont, grands navires). NASA GIBS publie les produits calibrés en images colorées avec leur palette : la plateforme **décode la palette** pour retrouver la grandeur physique (radiance en nW·cm⁻²·sr⁻¹, température de brillance en K).

| Étape | Règle |
| --- | --- |
| Mer | ZEE tramée sur une grille de 0,0045° (~500 m), érodée de 2 km : lumières de ports et de villes exclues |
| Contraste | CFAR local (fond 31 px) : ≥ `night_light_k` écarts-types (6) et ≥ 2 nW·cm⁻²·sr⁻¹ au-dessus du fond |
| Taille | ≤ 9 pixels : une ville ou un nuage éclairé par la lune est plus grand |
| Nuages | bande thermique **I5** : si le pixel le plus froid à ± 3 px est plus froid que la mer de la nuit (médiane) de 4 K, ou sous 285 K, c'est un nuage éclairé |
| Heure | heure nominale du passage (± 1 h) : les produits quotidiens ne portent pas l'heure au pixel |

Validation (nuits des 20 et 21 octobre 2026) : 2 lumières en ciel clair dans la rade de Cayenne (NOAA-20), une source forte (38 nW) à 200 km au large et quelques lumières faibles en confiance réduite (Suomi NPP).

## Veille automatique

Chaque heure (`sensor_watch_enabled` = 1) : les nuits VIIRS des deux derniers jours non encore traitées (6 h après le passage, délai de publication GIBS), puis jusqu'à **deux** scènes optiques nouvelles (Sentinel-2 des 3 derniers jours, Landsat des 8 derniers jours) sous la couverture nuageuse maximale `optical_max_cloud`. Une scène est lue sur au plus `OPTICAL_MAX_WINDOWS` fenêtres de 1024 pixels, choisies parmi les plus riches en eau dégagée et dont le centre est dans la ZEE. Chaque scène n'est traitée qu'une fois ; chaque scène traitée est inscrite dans le [journal d'audit](../audit.md) (`sensor.scan`).

## Paramètres

`sensor_watch_enabled`, `optical_max_cloud`, `optical_cfar_k`, `night_light_k`, `fusion_gate_speed_kn` : voir [Paramètres opérationnels](../administration/parametres.md).

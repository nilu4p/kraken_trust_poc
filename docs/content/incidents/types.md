# Types d'incident

| Type (libellé affiché) | Code | Origine | Déclencheur | Score / sévérité |
| --- | --- | --- | --- | --- |
| **Radar : navire sans AIS** | `dark_vessel` | Scan Sentinel-1 | Écho radar fiable que ni AIS ni VMS n'explique. Titre « Navire sans AIS » si des positions AIS existaient au moment du passage, « **Écho radar non identifié** » sinon (absence d'AIS non démontrée) | [score de risque](../moteur/risque.md) calculé |
| **AIS : pêche étrangère** | `foreign_fishing` | Règle 24/7 | Navire de pêche sous pavillon étranger, sans VMS, dans la ZEE | 85 en mer territoriale (P1), 70 en zone contiguë (P2), 60 en ZEE (P2) |
| **AIS : liste INN** | `watchlist_ais` | Règle 24/7 / scan | Navire de la liste de surveillance, ou inscrit INN selon GFW Insights, émettant dans la ZEE | 90 (P1) |
| **AIS : coupure suspecte** | `ais_gap` | Règle 24/7 | Navire qui émettait régulièrement en zone côtière puis se tait | 75 si pêche ou étranger (P1), sinon 60 (P2) |
| **AIS : rendez-vous en mer** | `encounter` | Règle 24/7 | Deux navires lents côte à côte assez longtemps (transbordement possible) | 70 si pêche ou étranger (P2), sinon 50 (P3) |
| **Navire sous sanctions** | `sanctions` | Règle 24/7 | Correspondance OpenSanctions au-dessus du seuil | 85 (P1) |
| **Pêche en aire protégée** | `mpa_fishing` | Règle 24/7 | Navire de pêche lent dans une aire marine protégée | 75 si étranger (P1), sinon 65 (P2) |

Les conditions exactes et leurs seuils réglables sont détaillés dans [Règles 24/7](../moteur/regles.md).

!!! info "Un incident par navire et par jour"
    Les incidents issus de l'AIS sont rattachés **au navire** (son MMSI), pas à une distance : tant qu'un incident du même type est ouvert pour ce navire, les nouvelles occurrences s'y ajoutent comme observations au lieu d'en créer d'autres.

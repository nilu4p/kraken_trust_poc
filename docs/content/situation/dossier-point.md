# Dossier du point (clic droit)

<p class="where">Clic droit n'importe où sur la carte. Fenêtre « Dossier du point ».</p>

Tout ce que la plateforme sait d'un endroit, sans qu'il y ait de cible : utile pour évaluer un signalement (appel radio, observation aérienne) ou préparer une patrouille.

| Ligne | Contenu | Source |
| --- | --- | --- |
| **Coordonnées** | Latitude, longitude du point cliqué | — |
| **Zone** | Mer territoriale, zone contiguë, ZEE, ou « Hors ZEE / terre » | Marine Regions |
| **Fond** | Profondeur en mètres, ou « terre émergée » ; chlorophylle (mg/m³) et mention **eaux productives** si ≥ 1 mg/m³ | NOAA ETOPO, Copernicus Marine (NOAA VIIRS en secours) |
| **Océan (Copernicus Marine)** | Température de surface (°C), **front thermique** et son gradient (°C/10 km) si au-dessus du seuil, courant (nœuds et direction vers laquelle il porte, en degrés : 0 = nord, 90 = est) | Copernicus Marine |
| **Aire protégée** | Nom et statut si le point est dans une aire marine protégée | WDPA |
| **Limites de pêche (SHOM)** | Distance aux limites des 100 NM et des 3 NM | SHOM |
| **Mer** | Vent (m/s) et houle (m) | Open-Meteo |
| **Événements GFW (25 km)** | Nombre de coupures AIS, rencontres, flânages, pêches dans un rayon de 25 km sur 30 jours | Global Fishing Watch |
| **Incidents à moins de 25 km** | Incidents existants, avec distance, statut et sévérité (lien) | KRAKEN |

Le chargement peut prendre une vingtaine de secondes la première fois (service de bathymétrie lent) ; les réponses sont ensuite mises en cache.

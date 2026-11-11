# Fiche cible radar

<p class="where">Clic sur une cible radar (rond rouge ou jaune), ou sur une ligne de l'onglet <em>Cibles</em>. Volet glissant à droite de la carte.</p>

En-tête : identifiant de la cible (`SAR-001` = 1re cible de la scène, par ordre de rapport signal/bruit), boutons **‹ ›** pour passer à la cible précédente / suivante, **✕** pour fermer.

## De haut en bas

### 1. Vignette radar

Extrait de l'image Sentinel-1 centré sur la cible (aperçu : non compté comme consultation de preuve ; cliquer l'image ouvre le fichier et la consultation est tracée). Un navire apparaît comme un **point ou un trait brillant** sur fond de mer sombre ; un sillage peut former une ligne. Lien **Planche scellée de l'alerte** : image de toutes les cibles de la scène.

### 2. Badge de scellement C2PA

| Badge | Signification |
| --- | --- |
| <span class="sw sq" style="--c:#3dff8a"></span> **Preuve intègre — manifeste C2PA signé par le certificat de l'instance KRAKEN** | L'image porte un manifeste signé par l'autorité de certification de la plateforme et n'a pas été modifiée |
| <span class="sw sq" style="--c:#f5c542"></span> **Manifeste signé — signature KRAKEN sans C2PA embarqué** | Le C2PA n'a pas pu être embarqué ; le manifeste joint est signé par la plateforme et les fichiers concordent |
| <span class="sw sq" style="--c:#fb923c"></span> **Non vérifiable** | Ancienne preuve au manifeste non signé : à re-sceller ou à ne pas produire seule |
| <span class="sw sq" style="--c:#ff3b3b"></span> **Preuve altérée — intégrité rompue depuis le scellement** | Le fichier ne correspond plus à ce qui a été scellé : **ne pas utiliser comme preuve** |

Voir [Preuves](../preuves.md).

### 3. Évaluation

- **Badge de risque** : `Risque Élevé · 62/100` (niveaux dans la [légende](legende.md#niveaux-du-score-de-risque-badges)).
- **Badge de fiabilité** : `Détection fiable · 84 %`.
- **Liste à puces (raisons du risque)** : chaque facteur qui a ajouté des points, en clair (« Dans la mer territoriale (12 NM) », « 2 coupure(s) AIS GFW à moins de 30 km (30 j) », « Front thermique… »). Détail : [Score de risque](../moteur/risque.md).
- **Liste grise (constats de fiabilité)** : ce qui a augmenté ou diminué la confiance (« Confirmé en double polarisation », « Haut-fond (~2 m) : roche ou banc de vase possible »). Détail : [Indice de fiabilité](../moteur/fiabilite.md).

### 4. Incident

Référence (`KRK-2026-0042`), statut, sévérité et lien vers le dossier. Si l'incident est **Nouveau** et que vous êtes analyste ou plus :

- **Prendre en charge** → passe l'incident *En triage* et l'horodate (fin du délai de triage) ;
- **Fausse alarme** → clôt l'incident avec la résolution *Fausse alarme*. L'endroit est retenu : une nouvelle détection à moins de 250 m perdra 0,45 de fiabilité.

### 5. Caractéristiques

| Champ | Définition |
| --- | --- |
| **Coordonnées** | Latitude / longitude du pixel le plus brillant de la cible (degrés décimaux, WGS 84) |
| **Indice de confiance** | Force du signal radar seule, de 50 à 99 % : `0,5 + 0,49 × tanh( max(0, SNR − k) / 2k )`. À ne pas confondre avec la **fiabilité**, qui intègre tout le contexte |
| **Zone** | Mer territoriale (12 NM), zone contiguë (24 NM) ou ZEE (200 NM) |
| **Taille estimée** | Longueur de la tache radar le long de son grand axe, moins ~20 m d'étalement radar (la résolution de Sentinel-1 est d'environ 20 m pour des pixels de 10 m : chaque écho déborde d'une cellule). « ×3,1 » = **allongement** (rapport grand axe / petit axe) : un navire est allongé (≥ 2), le bruit est compact. Estimation à ± 10 – 20 m : le sillage ou une mer agitée peuvent encore l'allonger. Sous 6 pixels, la taille est **non mesurable** (affichée « non mesurable (n px) ») et ne compte pas dans le risque. Détail : [Détection radar](../moteur/detection-sar.md#mesures-sur-chaque-tache) |
| **Polarisation** | *Double ✓* : la cible apparaît sur les deux polarisations (VH et VV) — confirmation forte. *Double ✗* : absente de la seconde — suspect. *Simple* : une seule polarisation disponible |
| **Source AIS** | Flux utilisé pour vérifier qu'aucun navire coopératif n'était là : *aisstream.io (historique)*, *AISHub + aisstream.io (historique)*, *VMS (historique)* (positions enregistrées au moment du passage), *aisstream.io (temps réel, passage récent)* (scène de moins de 3 h), **Aucune position AIS au moment du passage** (scène plus ancienne sans historique : l'absence d'AIS n'est pas démontrée, risque pondéré ×0,85) ou **Simulé — à confirmer** (aucune clé AIS, instance de développement uniquement) |
| **Passage Sentinel-1** | Date et heure UTC de l'acquisition radar |
| **Scellement** | Date et heure de création du dossier de preuves |
| **Mode SAR** | Mode d'acquisition (IW : bande de 250 km, 10 m de résolution) |
| **Source STAC** | Identifiant de la scène Sentinel-1 dans le catalogue Copernicus (permet de retrouver l'image d'origine) |
| **Empreinte PKI (SHA-256)** | Empreinte du certificat racine qui a signé la preuve : permet à un tiers de vérifier qu'elle vient bien de cette instance |

### 6. Imagerie autour de la cible (±3 j)

Recherche automatique, dans les catalogues Copernicus et CNES, des images couvrant la cible **3 jours avant ou après** le passage radar :

- vignettes **OPT** (optique) ou **SAR** avec l'écart au passage radar (`+5 h`, `−1 j`) ;
- liste : capteur, **résolution** (m), date, écart, **couverture nuageuse** ☁ ;
- **produit ↗** : page du produit (téléchargement selon vos droits) ;
- les noms en **vert-jaune** sont des images **très haute résolution** (Pléiades, Pléiades Neo, SPOT, WorldView, TerraSAR-X, COSMO-SkyMed, ICEYE…) : téléchargement réservé aux comptes Copernicus éligibles. Pour **programmer** une prise de vue Pléiades : DINAMIS (CNES / IGN).

Les empreintes de ces images s'affichent sur la carte (contour vert-jaune).

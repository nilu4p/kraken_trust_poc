# Détection radar (SAR / CFAR)

## Le radar Sentinel-1 en bref

- **Radar à synthèse d'ouverture (SAR)** : le satellite émet des ondes radio (bande C, 5,4 GHz) et mesure l'écho. Il voit **de jour comme de nuit, à travers les nuages**.
- La **mer** renvoie peu d'énergie vers le satellite (surface lisse qui réfléchit ailleurs) : elle apparaît **sombre**. Une **coque métallique** forme un réflecteur qui renvoie beaucoup d'énergie : elle apparaît comme un **point brillant**.
- Produit utilisé : **GRD**, mode **IW** — bande de 250 km, pixel de 10 m.
- **Polarisations** : l'onde est émise et reçue horizontalement ou verticalement. **VV** (co-polarisation) et **VH** (polarisation croisée). Les navires ressortent mieux en **VH**, où la mer est encore plus sombre ; la détection se fait en VH (ou HV) et la confirmation en VV (ou HH).
- Revisite sur la Guyane : quelques passages par semaine. D'où les [règles 24/7](regles.md) entre les passages.

## Étapes

1. **Recherche** de la scène dans le catalogue STAC Copernicus (`stac_lookback_days` jours en arrière au plus).
2. **Lecture à distance** de l'image, uniquement par **tuiles de 1 024 × 1 024 pixels** dont le centre est dans la ZEE (`sar_max_windows` tuiles au plus, réparties) : pas besoin de télécharger l'image entière (≈ 1 Go). Deux chemins pour **les mêmes pixels** :
    - **Copernicus S3** (clés `ACCESS_KEY` / `SECRET_KEY`) ;
    - **miroir Microsoft Planetary Computer**, **sans clé** (jeton anonyme gratuit) : utilisé automatiquement quand aucune clé Copernicus n'est configurée, et **en secours** sur la même scène si la lecture S3 échoue (clés expirées, quota, réseau). Le chemin utilisé est affiché pendant le scan et scellé dans la preuve (`pixel_source`). Paramètre `SAR_SOURCE` : `auto` (défaut), `copernicus` ou `planetary`.
3. **Géoréférencement** par les quelque 200 points d'appui (GCP) fournis avec l'image, interpolés par *thin-plate spline* : erreur médiane ≈ 3 m (contre 68 m, et jusqu'à 260 m, avec un simple ajustement affine sur toute la scène).
4. **Masquage des terres** : la terre, très claire en polarisation croisée, est retirée de l'image avant la détection (voir plus bas).
5. **Détection CFAR** sur chaque tuile.
6. **Extraction des taches** (pixels détectés connexes) et mesures.
7. **Confirmation en double polarisation**.
8. **Filtrage** : taches hors ZEE, sur la côte, sur un îlet, un ouvrage ou une balise écartées ; on garde les 25 plus fortes (`max_detections`).
9. **Avis xView3** (facultatif, `make up-ai`) : un modèle d'IA donne, pour chaque cible retenue, ses pourcentages « objet », « navire », « pêche » et une longueur — **en information seule**, sans effet sur la suite. Voir [Avis de l'IA](ia.md).

## CFAR à moyenne de cellules (CA-CFAR)

Un seuil unique ne marche pas : la mer est plus claire là où le vent souffle fort, près du bord de l'image, dans les courants. Le **CFAR** (*taux de fausses alarmes constant*) compare chaque pixel **à la mer qui l'entoure** :

```text
            ┌──────────── fenêtre de fond 41 × 41 px ────────────┐
            │      ┌──── zone de garde 11 × 11 px ────┐          │
            │      │          pixel testé             │          │
            │      └──────────────────────────────────┘          │
            └────────────────────────────────────────────────────┘
 μ, σ = moyenne et écart-type des pixels de la couronne (fond − garde)

 pixel détecté  ⇔  valeur > μ + k × σ          (k = cfar_k, 5 par défaut)
```

- La **zone de garde** empêche le navire lui-même (et son étalement) de rehausser son propre seuil.
- Les pixels sans donnée (bords de l'image) sont exclus ; s'il reste moins d'un quart de pixels valides, le pixel n'est pas testé.
- σ est plancher à 5 % de μ (mer très homogène).
- Calcul rapide par **images intégrales** (sommes cumulées) : toute l'image est traitée en quelques secondes.

**Effet de `k`** : à 5, un pixel doit être plus brillant que la mer locale de 5 écarts-types. Plus haut = moins de fausses alarmes mais petites embarcations en bois perdues ; plus bas = plus de cibles faibles mais plus d'échos de vagues.

## Mesures sur chaque tache

| Mesure | Calcul | Usage |
| --- | --- | --- |
| **Taille (pixels)** | nombre de pixels connexes (8-connexité) ; gardé si 2 ≤ n ≤ 600 | 1 pixel = chatoiement ; > 600 = côte, île, grand objet |
| **Longueur estimée** | étendue de la tache le long de son axe principal × 10 m, **moins 20 m** d'étalement radar, et au moins 10 m | taille de la coque : la résolution de Sentinel-1 IW (~20 m × 22 m pour des pixels de 10 m) étale chaque écho d'environ une cellule. Un navire en biais n'est pas allongé de √2 comme avec la diagonale du rectangle englobant |
| **Allongement** | rapport des axes principaux de la tache | navire ≥ 2 ; bruit ≈ 1 |
| **SNR local** | (valeur du pixel le plus brillant − μ) / σ | force de la cible |
| **Indice de confiance** | 0,5 + 0,49 × tanh( max(0, SNR − k) / 2k ) | de 50 % (juste au seuil) à 99 % |
| **Bord de fauchée** | pixel dans les 3 % extrêmes de la largeur de l'image | zone bruitée |

## Côte, îlets, ouvrages et balises

Près de la côte, les fausses alarmes sont nombreuses : lisière de mangrove, rochers, îlets, pieux, digues, bouées des chenaux. Trois couches indépendantes les écartent.

| Couche | Ce qu'elle apporte | Limite |
| --- | --- | --- |
| **Image radar du jour** | La terre (forêt, mangrove, ville) est 6 dB au moins plus claire que la mer sur de grandes surfaces. Elle est retirée **avant** le CFAR : pas de cible sur terre, et une mer mieux mesurée près du rivage. Suit le vrai rivage du jour, y compris les bancs de vase qui se déplacent | ne voit pas les îlets de moins de ~300 m ; appliquée seulement à moins de 3 km d'une côte cartographiée, pour qu'une cellule de pluie en mer ne masque jamais un navire |
| **Trait de côte OpenStreetMap** | Précis à quelques mètres, avec les îlets et les rochers ; remplace la grille de bathymétrie ETOPO (~1,8 km), qui classait « terre » des points à 2 – 3 km au large de la côte de vase | tracé à une date donnée : la côte guyanaise bouge |
| **Ouvrages et balisage OpenStreetMap** | Appontements, digues, épis, quais, plateformes, phares ; bouées latérales et cardinales des chenaux de Cayenne et Kourou, balises, coffres d'amarrage | uniquement ce qui est cartographié |

| Règle (écho **écarté**, compté dans le compte rendu du scan) | Seuil |
| --- | --- |
| Sur la terre vue par le radar, ou à moins de **80 m** de son rivage | lisière elle-même |
| Côté terre du trait de côte OSM, ou à moins de **50 m** de celui-ci | îlet, rocher, enrochement |
| À moins de **80 m** d'un ouvrage ou d'une balise cartographiés | bouée, balise, appontement |

Au-delà, la [fiabilité](fiabilite.md) baisse avec la proximité du rivage (−0,45 sous 200 m, −0,25 jusqu'à 500 m, −0,10 jusqu'à 1 km) et d'un ouvrage (−0,15 jusqu'à 300 m). La fiche de la cible affiche la distance au rivage, et « Pourquoi cette alerte ? » la condition « Écho en mer, pas sur la côte ».

**Validation** (scène réelle du 19 octobre 2026, toute la partie ZEE : 71 tuiles) : 130 échos écartés, soit 121 sur la lisière du rivage, 5 îlets ou rochers et 4 balises. Tous ont été vérifiés visuellement : aucun navire en mer parmi eux. Aucune cible retenue n'est à moins de 200 m du rivage.

Données OpenStreetMap © les contributeurs OpenStreetMap, licence ODbL. Une copie de la Guyane est livrée avec la plateforme (fonctionne hors ligne) et la maintenance horaire la met à jour tous les 30 jours.

## Double polarisation

La seconde polarisation est analysée avec un seuil un peu plus bas (`k − 1,5`, au moins 3). La cible est **confirmée** si un pixel détecté existe à moins de 2 pixels (20 m) dans la seconde image. Un vrai navire apparaît dans les deux ; beaucoup d'artefacts dans une seule.

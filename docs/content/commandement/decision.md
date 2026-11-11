# Aide à la décision

<p class="where">Dossier d'incident (onglet <strong>Incidents</strong>) → panneau <strong>Aide à la décision</strong>, pour tout incident non clos. Recalculé toutes les 2 minutes et sur <strong>Recalculer</strong>.</p>

Le panneau répond, dans l'ordre où un chef de quart se les pose, à quatre questions. Chaque chiffre dit d'où il vient ; les hypothèses sont listées en bas du panneau et reprises dans le [compte rendu](rapports.md).

## 1. Où est la cible maintenant ?

La dernière position connue est **projetée jusqu'à maintenant** à cap et vitesse constants. L'origine de la cinématique détermine la confiance :

| Cinématique | Quand | Incertitude (rayon) |
| --- | --- | --- |
| **AIS du navire** | incident ouvert sur un MMSI (règles 24/7) : la dernière position AIS du navire (48 h max) est relue à chaque calcul — l'incident suit le navire en direct | 0,5 NM + 10 % de la distance parcourue depuis la position |
| **Estimée entre deux observations** | au moins deux détections radar rattachées à l'incident, à 10 min – 48 h d'intervalle, vitesse ≤ 35 nœuds | 1 NM + 30 % de la distance parcourue ; elles peuvent concerner deux navires distincts |
| **Inconnue** | cible vue une seule fois, sans AIS | **datum** : 1 NM + *vitesse supposée* × temps écoulé |

La *vitesse supposée d'une cible sans AIS* (8 nœuds par défaut) se règle dans [Paramètres](../administration/parametres.md) (`decision_assumed_speed_kn`). Le rayon est plafonné à 250 NM.

## 2. Où va-t-elle ?

Pour une cible en route (≥ 0,5 nœud, cap connu), la route est suivie par pas de 10 minutes sur 72 h :

- **Sortie de la ZEE** : délai et point de sortie (en orange) — c'est souvent le facteur limitant ;
- **Approche de la côte** : la route mène à terre (entrée en fleuve, port) ;
- sinon : *reste dans la ZEE au moins 72 h*.

## 3. Qui peut l'atteindre ?

Pour chaque [moyen](moyens.md), l'interception est résolue exactement (équation du second degré) : le moyen quitte son **point de départ en mer** après son **délai d'appareillage**, file en ligne droite à sa **vitesse de transit**, et rejoint la cible qui continue sa route.

| Colonne | Calcul |
| --- | --- |
| **Sur zone dans** | appareillage + transit, depuis maintenant |
| **Distance** | longueur du transit (NM) |
| **Lieu d'interception** | zone juridique du point de rencontre : mer territoriale, zone contiguë, ZEE, ou hors eaux françaises |
| **Rayon de recherche** | incertitude actuelle, accrue de la dérive possible jusqu'à l'arrivée |
| **Réalisable** | moyen *disponible*, aller-retour ≤ distance franchissable, arrivée **avant la sortie de ZEE**, point de rencontre dans les eaux françaises, délai ≤ 48 h |

Une ligne non réalisable affiche ses **réserves** (moyen engagé, cible plus rapide, aller-retour trop long, arrivée après la sortie de ZEE…). Les moyens réalisables capables d'arraisonner sont classés en premier, par délai.

## 4. Que faire ?

Quatre options sont toujours présentées, avec leurs avantages et leurs risques ; une seule est **recommandée** :

| Option | Recommandée quand |
| --- | --- |
| **Interception et contrôle** (meilleur moyen pouvant arraisonner) | sévérité P1/P2, détection fiable, un moyen arraisonneur peut intercepter dans les eaux françaises |
| **Levée de doute aérienne** (avion, hélicoptère) | fiabilité de détection < 45 %, ou position inconnue vieille de plus de 6 h, et un aéronef peut être sur zone |
| **Signalement et coopération** (État du pavillon, ORGP, pays voisin) | aucun moyen ne peut atteindre la cible avant sa sortie, ou cible déjà hors des eaux françaises |
| **Surveillance renforcée sans engagement** | autres cas (P3/P4 notamment) |

Si l'interception est possible sur un P1/P2 mais que la fiabilité est faible, l'interception reste recommandée avec la consigne de **confirmer la cible pendant le transit**.

## Cadre juridique

Le panneau rappelle le régime de la zone où se trouve la cible (Convention des Nations unies sur le droit de la mer, CNUDM) :

| Zone | Repère |
| --- | --- |
| Mer territoriale (0-12 NM) | souveraineté ; la pêche prive le passage de son caractère inoffensif (art. 19 §2 i) |
| Zone contiguë (12-24 NM) | droits de la ZEE sur la pêche + contrôles douaniers, fiscaux, sanitaires, d'immigration (art. 33) |
| ZEE (24-200 NM) | arraisonnement, inspection, saisie, poursuites (art. 73 §1) ; mainlevée contre caution raisonnable (§2) ; pas d'emprisonnement pour la seule infraction de pêche (§3) ; notification à l'État du pavillon (§4) |
| Hors eaux françaises | uniquement par **droit de poursuite** commencé dans les eaux françaises et ininterrompu (art. 111), ou accord de l'État du pavillon |

Le **droit de poursuite** est rappelé dès qu'une sortie de ZEE est prévue ou qu'un point d'interception tombe hors des eaux françaises.

!!! warning "Repères indicatifs"
    Ces rappels ne remplacent ni la décision du délégué du gouvernement pour l'action de l'État en mer, ni l'avis du parquet.

## Limites connues

- Routes **en ligne droite** depuis le point de départ en mer : le contournement des terres et des hauts-fonds n'est pas modélisé (choisissez un point de départ **à la sortie du port**, voir [Moyens](moyens.md)).
- Vitesse et cap de la cible **constants** : un navire qui manœuvre invalide la projection — d'où le rayon de recherche.
- Tant que les moyens ont leurs **valeurs génériques**, un avertissement le rappelle.
- Rien n'est enregistré par ce panneau ; ce qui fait foi est le [compte rendu](rapports.md) émis, qui fige le calcul du moment.

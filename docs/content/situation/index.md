# Vue Situation

<p class="where">Onglet <strong>Situation</strong> — page d'accueil après connexion.</p>

La vue Situation est la **carte opérationnelle commune** : tout ce que la plateforme sait de la ZEE à un instant de référence — maintenant (**direct**) ou un instant passé jusqu'à 90 jours (**rejeu**), choisi sur la [frise chronologique](chronologie.md).

## Organisation de l'écran

| Zone | Contenu |
| --- | --- |
| **Barre du haut** | État du scan en cours (message et icône d'activité), choix de la scène Sentinel-1 et bouton **Scanner** (analyste et plus) — voir [Lancer un scan](scan.md) |
| **Frise chronologique** (bas de la carte) | Direct / rejeu, lecture, activité par source — voir [Chronologie et rejeu](chronologie.md) |
| **Carte** (centre) | Fond de carte, couches, cibles radar, navires AIS — voir la [légende](legende.md) |
| **Légende** (coin de la carte) | Version courte repliée ; cliquer pour la déplier |
| **Fiche cible** (volet glissant à droite) | S'ouvre au clic sur une cible radar — voir [Fiche cible](fiche-cible.md) |
| **Panneau latéral** (bord droit) | Rail d'icônes ou panneau déplié à 4 onglets : Carte, Couches, Contexte, Cibles — voir [Panneau latéral](panneau-lateral.md) |

## Interactions avec la carte

| Geste | Effet |
| --- | --- |
| Molette / pincement | Zoom |
| Glisser | Déplacer la carte |
| **Clic sur une cible radar** (rond coloré) | Ouvre la [fiche cible](fiche-cible.md) ; la carte se recentre pour que la cible ne soit pas cachée par le volet |
| **Clic sur un navire AIS** (flèche) | Bulle d'information + [fiche navire fusionnée](fiche-navire.md) |
| Clic sur un événement GFW (petit rond) | Bulle : type d'événement, navire, dates, durée de coupure |
| Survol d'une zone, d'une empreinte satellite ou d'une aire protégée | Infobulle descriptive |
| **Clic droit n'importe où** | [Dossier du point](dossier-point.md) : zone légale, fond, océan, météo, incidents proches |
| Flèches ‹ › de la fiche cible, ou touches ++arrow-left++ ++arrow-right++ | Passer à la cible précédente / suivante (ordre du risque) |
| ++escape++ ou ✕ | Fermer la fiche cible |

## Rafraîchissement

| Donnée | Fréquence |
| --- | --- |
| Navires AIS, couverture temporelle | toutes les **60 s** |
| État d'un scan en cours | toutes les **3 s** pendant le scan, puis rechargement des cibles à la fin |
| Cibles radar et incidents | au chargement, après un scan et après chaque action sur un incident |
| Couches lourdes (SAR GFW, effort de pêche, aires protégées, océan) | une fois, à la première activation |

## Préférences mémorisées

Le fond de carte, les couches cochées, le mode d'affichage, l'onglet du panneau, l'épinglage et les réglages visuels sont **enregistrés dans votre navigateur** : vous retrouvez votre carte telle que vous l'avez laissée. Ils ne sont pas partagés avec vos collègues et sont perdus si vous videz les données du site ou changez de poste. Le bouton *Rétablir l'affichage par défaut* (onglet Carte) remet les réglages visuels d'origine.

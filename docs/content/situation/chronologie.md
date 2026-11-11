# Chronologie : temps réel et rejeu

<p class="where">Bas de la carte, onglet <strong>Situation</strong> — tous les rôles.</p>

La plateforme décrit toujours la situation **à un seul instant de référence**. En **direct**, c'est maintenant. En **rejeu**, c'est l'instant choisi sur la frise, jusqu'à 90 jours en arrière. Toutes les couches, fiches et calculs suivent cet instant : la carte ne mélange jamais l'AIS d'aujourd'hui avec une détection radar d'avant-hier.

## La frise

| Élément | Rôle |
| --- | --- |
| **DIRECT** (vert, pulsant) | Situation en temps réel ; la carte se rafraîchit chaque minute |
| **REJEU · date** (orange) | Instant reconstitué ; la carte est encadrée en orange et un bandeau le rappelle en permanence |
| ⏮ / ⏪ / ⏩ / ⏭ | Reculer ou avancer d'un jour ou d'une heure |
| ▶ / ⏸ | Lecture : le temps défile à la vitesse choisie (10 min, 1 h, 6 h ou 1 jour par seconde). Depuis le direct, ▶ rejoue les dernières 24 h. Arrivée au présent, la lecture repasse en direct |
| Date et heure (UTC) | Saisie directe de l'instant |
| 24 h · 7 j · 30 j · 90 j | Étendue affichée par la frise |
| **Pistes d'activité** | Une piste par source, chacune à sa propre échelle (la hauteur d'une barre se compare à la même piste, jamais à une autre) |

Si le journal de collecte couvre la période, une piste **Collecte AIS** (grise) s'affiche en tête : hauteur = part des minutes où un flux AIS écoutait. Une période **sans aucune écoute** est **hachurée en rouge** sur toutes les pistes : là, une mer vide signifie « pas de données », pas « pas de navire ». Sur la carte, un bandeau rouge le rappelle dès que la collecte des 2 h précédant l'instant tombe sous 50 %.

Cliquer ou glisser sur les pistes déplace l'instant. Le survol d'une période affiche le détail chiffré de chaque piste, plus les incidents clos.

| Piste | Compte |
| --- | --- |
| Navires AIS | Navires distincts entendus (aisstream, AISHub, VesselAPI) |
| Scènes radar analysées | Scènes Sentinel-1 traitées par la plateforme |
| Détections radar | Échos radar rattachés à un incident (heure du passage satellite) |
| Incidents ouverts | Incidents créés |
| Règles 24/7 | Observations des règles continues (pêche étrangère, coupure AIS, rendez-vous, sanctions…) |
| Positions VMS | Navires sous licence ayant émis une position VMS |

## Ce que montre un rejeu

Le rejeu reconstitue **ce que la plateforme savait à cet instant**, pas ce qu'on sait aujourd'hui de cet instant :

| Élément | Au rejeu de l'instant T |
| --- | --- |
| Navires AIS | Dernière position de chaque navire **avant T** (2 h), trace des 6 h précédentes ; aucune position postérieure |
| Cibles radar | Seulement les détections **déjà analysées et scellées à T** (une scène traitée le lendemain n'apparaît pas encore) |
| Incidents | **Statut et sévérité à T**, rejoués depuis l'historique de chaque incident ; les incidents ouverts après T n'existent pas encore |
| Événements GFW, détections SAR GFW, effort de pêche | Fenêtre de 30 jours **se terminant à T** ; rien de postérieur |
| Océan (Copernicus Marine) | Cartes et valeurs **du jour de T** (courants : pas de 6 h) ; une date hors de la plage du produit donne « inconnu », jamais la carte du jour |
| Météo (Open-Meteo) | Archive horaire de T ; indisponible = « inconnue », jamais la météo actuelle |
| Imagerie NASA | Mosaïque du jour de T (lumières nocturnes : dernière mosaïque complète du satellite) ; GOES-Est : image de 10 min la plus récente à T |
| Sentinel-2 (10 m) | Dernière image par tuile acquise dans les 10 jours précédant T |
| Scènes Sentinel-1 | Acquisitions des 12 jours précédant T |
| Fiche navire | Flux et trace des 24 h avant T, incidents au statut de T. Le renseignement (registre, GFW Insights, sanctions) reste celui d'aujourd'hui : c'est indiqué |
| Dossier du point | Zone, océan, météo, événements GFW et incidents à T |

!!! warning "Rejeu en lecture seule"
    En rejeu, les boutons d'action sur un incident (prise en charge, fausse alarme) et le lancement d'un scan sont désactivés : le statut affiché est celui du passé. Revenez au **direct** pour agir.

!!! info "Profondeur d'historique"
    90 jours (`AIS_RETENTION_DAYS`) : au-delà, les positions sont purgées et l'instant est refusé. Les sources externes ont leurs propres limites : la chlorophylle Copernicus quasi temps réel ne remonte qu'une dizaine de jours, les détections SAR GFW ont environ 5 jours de délai.

## Cohérence des alertes

Le même principe s'applique au calcul des alertes. Une scène radar analysée plusieurs jours après son acquisition est évaluée **avec le contexte de l'heure du passage** :

- état de la mer de l'heure du passage, **au voisinage de chaque cible** (maille de 0,25°), et non un point unique pour toute la ZEE ;
- océan (courants, fronts thermiques, chlorophylle) du jour du passage ;
- événements GFW et détections SAR GFW des 30 jours précédant le passage, jamais postérieurs ;
- positions AIS les plus proches du passage, extrapolées à l'instant d'acquisition ([corrélation AIS](../moteur/correlation-ais.md)).

Sans cela, une même cible changeait de score selon le jour de l'analyse. Exemple mesuré : la chlorophylle d'un même point passait de 0,9 à 2,2 mg/m³ en six jours, ce qui ajoutait à tort +5 points de risque « eaux productives » à une détection plus ancienne.

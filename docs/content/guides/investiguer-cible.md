# Investiguer une cible radar

Incident type **Radar : navire sans AIS**. Objectif : décider en quelques minutes s'il s'agit d'une fausse alarme, d'une activité légitime ou d'un navire suspect.

## Étape 1 — Est-ce un navire ?

Ouvrez la cible (carte ou *Cibles*) et regardez la [fiche](../situation/fiche-cible.md) :

1. **Badge de fiabilité** : *Fiable* (≥ 70 %) → passez à l'étape 2. *Douteuse* → lisez les constats en gris.
2. **Vignette radar** : un navire est un point ou un trait **net et brillant**, souvent avec une petite « croix » (lobes secondaires) ; un sillage peut former une ligne sombre ou claire derrière lui. Un nuage de points diffus, une tache floue sur une zone de vagues ou un écho collé à la côte sont suspects.
3. **Constats** : *Haut-fond*, *terre émergée*, *cible fixe probable*, *fausse alarme déjà signalée* → très probablement pas un navire.
4. **Polarisation** : *Double ✓* est un fort argument pour un vrai navire.

Pas un navire → **Fausse alarme**. C'est fini.

## Étape 2 — Pourquoi n'avait-il pas d'AIS ?

1. **Source AIS** dans la fiche : *Simulé* signifie qu'on ne sait pas. Vérifiez l'AIS réel (étape 3) avant toute conclusion.
2. Sur la carte, regardez les **flèches AIS** proches au même moment : un navire AIS tout près mais non apparié peut indiquer une position AIS ancienne (navire rapide) — l'écho est peut-être ce navire. Classez alors en *Activité légitime* ou *Doublon*.
3. Petites embarcations (≈ 10–15 m, pirogues, tapouilles) : souvent **sans obligation d'AIS**. Le risque dépend alors surtout de la zone et des comportements.

## Étape 3 — Croiser les sources

Suivez le **playbook** du dossier :

| Vérification | Où | Ce qu'on cherche |
| --- | --- | --- |
| Historique GFW | couche *Événements GFW*, dossier du point | coupures AIS, rencontres, pêches proches récentes |
| Détections SAR GFW | couche *Détections SAR GFW* | losanges roses récurrents = zone d'activité non déclarée |
| Imagerie optique | section *Imagerie autour de la cible* | image Sentinel-2 ou THR à quelques heures : forme du navire, sillage, couleur |
| Lumières nocturnes | *Carte → Lumières nocturnes VIIRS*, date du passage | grappes lumineuses = flottille de pêche de nuit |
| Océan | dossier du point (clic droit) | front thermique, eaux productives = zone de pêche |
| Aire protégée, limites | couches | dans une AMP ou en deçà des 3 NM |

## Étape 4 — Décider

| Conclusion | Action |
| --- | --- |
| Pas un navire | Clore : **Fausse alarme** |
| Navire identifié et autorisé | Clore : **Activité légitime** (note : identité, source) |
| Même navire qu'un autre incident | Clore : **Doublon** (note : référence de l'autre incident) |
| Navire suspect | **Qualifier**, note de synthèse ; un superviseur décide de l'escalade |

!!! tip "Une bonne note de qualification"
    *Quoi* (taille, type probable), *où* (zone, distance à la côte), *quand* (passage UTC), *pourquoi suspect* (sans AIS, AMP, coupures GFW, flottille VIIRS), *quelles preuves* (dossier, images), *proposition* (surveillance au prochain passage, moyen à engager).

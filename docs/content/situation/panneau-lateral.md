# Panneau latéral

<p class="where">Bord droit de la vue Situation.</p>

## Replié ou déplié

- **Replié** : un rail étroit toujours visible, avec une icône par onglet (*Carte, Couches, Contexte, Cibles, Activité*). Sous *Cibles*, deux compteurs : nombre de cibles **prioritaires** (rouge) et **à vérifier** (jaune). Cliquer une icône déplie le panneau sur cet onglet ; la flèche ‹ le déplie sur le dernier onglet.
- **Déplié** : 320 px de large. Sur un écran étroit, il passe **par-dessus** la carte.
- **Repli automatique** : en mode *Repli auto*, le panneau se replie **12 secondes** après que la souris l'a quitté, à condition qu'aucun champ à l'intérieur ne soit en cours d'utilisation (une liste déroulante ouverte ou un calendrier ne le font jamais se replier).
- **Épingler** (bouton en haut à gauche du panneau) : le panneau reste ouvert en permanence. Ce choix est mémorisé.
- **›** (en haut à droite) : replier immédiatement.

## Onglet Carte

Tout ce qui touche au **visuel**.

### Fond de carte

| Fond | Source | Usage |
| --- | --- | --- |
| **Carte (OpenStreetMap)** | OpenStreetMap | Repérage : côtes, ports, toponymes. Rendu assombri pour la salle d'opérations |
| **Sentinel-2 sans nuages (EOX)** | mosaïque annuelle EOX | Vraie couleur, sans nuages : bancs de vase, panaches, estuaires |
| **VIIRS couleurs vraies du jour (NASA GIBS)** | NASA, image du jour choisi | Situation météo et nuageuse réelle d'une date donnée |

- **Date de l'imagerie NASA** : apparaît quand un fond ou une surcouche NASA datée est actif ; choisissez le jour (au plus tard la dernière image publiée, en général la veille).
- **Luminosité du fond** (40 – 160 %) et **Opacité du fond** (20 – 100 %) : n'affectent que le fond, jamais les symboles. Baisser l'opacité fait ressortir les couches.

### Satellite géostationnaire (GOES-Est)

- **GOES-Est — image toutes les 10 min** : image du satellite géostationnaire couvrant la Guyane, celle des 10 minutes les plus récentes publiées à l'instant de la frise (en direct, avec environ 30 min de délai de publication NASA). Couverture de toute la ZEE à ~1–2 km : nuages, grains, état du ciel au moment d'un passage radar. Trop grossière pour voir un navire.
- **Canal** : *Auto* (couleurs vraies le jour, infrarouge la nuit), *GeoColor* ou *Infrarouge* (bande 13 : sommets nuageux froids en clair, de jour comme de nuit).
- **Opacité**. L'heure exacte de l'image est affichée sous les réglages et dans l'attribution de la carte.

### Imagerie satellite nocturne

- **Lumières nocturnes VIIRS (flottilles de pêche)** : bande jour/nuit (Day/Night Band) du capteur VIIRS de la NASA, nuit précédant la date choisie. Les bateaux éclairés apparaissent comme des points lumineux **par-dessus le fond de carte** (le noir de l'image est rendu transparent).
- **Satellite** : **NOAA-20** (par défaut) ou **Suomi-NPP**, deux passages de nuit à environ 50 minutes d'écart. NOAA-20 est publié avec un jour de retard, Suomi-NPP avec deux : la mosaïque Suomi-NPP de la veille est encore en cours d'assemblage chez la NASA (tuiles vides ou grises tramées, qui formaient un voile blanchâtre sur la carte). La console affiche donc automatiquement la **dernière mosaïque complète** de chaque satellite et l'indique en orange (« mosaïque du … affichée »). Pas de mosaïque Suomi-NPP sur NASA GIBS avant le 16/07/2026.
- **Intensité des lumières** : opacité de cette surcouche.

!!! tip "Aucune lumière ?"
    Une nuit sans navire éclairé est possible, mais une carte totalement vide signifie en général que la mosaïque n'est pas encore publiée : reculez la date d'un jour ou changez de satellite. La couverture nuageuse épaisse masque aussi les lumières.

### Océan (Copernicus Marine)

| Choix | Ce qui est affiché | Comment le lire |
| --- | --- | --- |
| Aucune | — | — |
| **Température de surface** | SST quotidienne (produit OSTIA) | Les fortes variations sur une courte distance sont des **fronts thermiques** où se concentrent poissons et flottilles |
| **Chlorophylle-a** | concentration quotidienne, échelle logarithmique | Vert intense = eaux productives (panache de l'Amazone) |
| **Courants de surface** | vitesse en couleur et flèches de direction (prévision 1/12°) | Le courant des Guyanes porte vers le nord-ouest à environ 2 nœuds : il explique la dérive d'une cible entre deux passages |

**Opacité de la surcouche** : de 20 à 100 %. Données Copernicus Marine en accès libre ; leur résolution est d'environ 4 à 9 km, elles ne s'affinent donc pas au-delà d'un certain zoom.

### Affichage

| Option | Effet |
| --- | --- |
| **Affichage standard** | Rendu par défaut |
| **Vision nocturne (NVG)** | Fond de carte teinté en vert et assombri (les symboles gardent leurs couleurs) : préserve la vision de nuit en salle obscure ou à la passerelle |
| **Contraste élevé** | Fond de carte en niveaux de gris assombri et traits des limites épaissis : les symboles ressortent au maximum (vidéoprojecteur, écran en plein soleil) |
| **Symboles petits / normaux / grands** | Taille des cibles et des flèches AIS |
| **Afficher la légende** | Montre ou masque la légende sur la carte |
| **Recentrer sur la cible sélectionnée** | Quand une cible est choisie (carte ou liste), la carte se déplace et zoome au besoin pour la montrer hors du volet de la fiche |
| **Rétablir l'affichage par défaut** | Remet tous les réglages visuels d'origine |

## Onglet Couches

Cases à cocher des **données** affichées. Chaque ligne montre les pastilles de couleur de la couche. Le détail de chaque symbole est dans la [légende](legende.md).

| Couche | Par défaut | Remarque |
| --- | --- | --- |
| Frontières maritimes | ✅ | ZEE, 24 NM, 12 NM |
| Limites de pêche SHOM (100 NM, 3 NM) | ✅ | |
| Aires marines protégées (WDPA) | ✅ | |
| Isobathes 20 à 4 000 m (ETOPO) | ☐ | courbes de profondeur calculées sur la grille bathymétrique déjà en cache ; le **rebord du plateau (200 m)** est tracé en clair |
| Navires AIS (temps réel) | ✅ | 2 dernières heures |
| Traces AIS (6 h) | ✅ | |
| Détections SAR GFW (*N* j) | ☐ | chargement long (jusqu'à 2 min) |
| Effort de pêche AIS GFW (*N* j) | ☐ | chargement long ; orange = pavillon étranger, bleu = français |
| Événements GFW | ✅ | compteurs par type sous la case ; motif affiché si indisponible (token manquant) |
| Empreintes Sentinel-1 | ☐ | |
| Images optiques de la cible | ☐ | |
| Sentinel-2 10 m (dernière image avant l'instant) | ☐ | contours des scènes (date, nuages) ; l'image apparaît à partir du zoom 9 ; première ouverture d'une zone : quelques secondes par tuile, puis cache |

**Période des couches GFW (SAR, effort)** : 7, 30 (défaut) ou 90 jours avant l'instant de référence. Ce choix est mémorisé.

## Onglet Activité

Rapport d'activité de pêche de toute la ZEE sur une période : voir [Rapport d'activité de la ZEE](activite.md).

## Onglet Contexte

### Couverture temporelle

Dit **ce qui surveille la ZEE en ce moment et depuis quand**. Pastille verte = à jour, jaune = ancien ou dégradé, grise = inactif ou non configuré.

| Ligne | Vert si… | Détail affiché |
| --- | --- | --- |
| **Radar Sentinel-1** | dernier scan il y a moins de 48 h | âge du dernier scan, nombre de scènes analysées sur 7 jours |
| **AIS continu** | dernier signal reçu il y a moins de 15 min | navires entendus dans l'heure, âge du dernier signal ; **« connecté, aucun message depuis N min »** = le flux fonctionne mais aucun récepteur aisstream ne couvre la zone ; « déconnecté (cause) » ; gris : clé aisstream manquante |
| **AISHub (2e flux)** | données reçues sur 24 h | navires sur 24 h, âge de la dernière position (affiché si configuré) |
| **VMS importé** | positions VMS sur 24 h | navires sous licence sur 24 h (affiché s'il y a des données) |
| **VesselAPI (3e flux)** | données reçues sur 24 h | navires sur 24 h, âge de la dernière position (affiché si le flux est activé) |
| **Renseignement navire** | token GFW, clé OpenSanctions ou VesselAPI présents ; orange si le budget VesselAPI du mois est épuisé | services actifs pour le criblage des navires, appels VesselAPI consommés / budget |
| **Notifications** | dernier envoi réussi | âge du dernier envoi, ou cause du dernier échec (le serveur de démonstration ne garde ses messages qu'en mémoire) |
| **Océan (Copernicus Marine)** | service activé | — |
| **Règles 24/7** | exécutées au moins une fois | âge de la dernière exécution (normalement < 1 min) |
| **Global Fishing Watch** | token configuré | détections SAR sans AIS et effort chargés |

Sous le tableau, une alerte orange résume la situation AIS du moment : nombre de **pêcheurs étrangers** et de navires **listés INN** visibles.

### État de la mer

Données Open-Meteo au centre de la ZEE : **vent** (m/s), **houle** (m), **température de surface** (°C), **courant** (km/h), et le verdict **SAR** sur la fiabilité de la détection radar dans ces conditions :

| Verdict | Condition | Effet |
| --- | --- | --- |
| Mer calme | vent < 3 m/s | aucun : fond radar sombre, meilleur contraste des navires |
| Conditions favorables | vent 3 – 7 m/s et houle ≤ 2 m | aucun |
| Mer agitée | vent > 7 m/s ou houle > 2 m | fiabilité −0,05 |
| Mer forte | vent > 12 m/s ou houle > 3,5 m | fiabilité −0,15 |

### Sources

Liste des sources avec leur état : **libre** (sans clé), **clé OK**, ou **configurer** (lien vers *Administration → Clés & intégrations* pour un administrateur).

## Onglet Cibles

Liste des cibles radar, groupées en **Prioritaires** puis **À vérifier**, triées par risque décroissant.

**Période des cibles radar** : 7, 30 (défaut), 90 derniers jours ou tout l'historique, d'après la date du passage satellite. Les cibles dont l'incident est **encore ouvert** restent affichées quelle que soit la période. Une scène analysée deux fois n'affiche chaque cible **qu'une fois**.

Chaque ligne :

- référence de l'incident (ex. `KRK-2026-0042`) ou identifiant de la cible ;
- **sévérité** de l'incident (P1 – P4) et **score de risque** ;
- **statut** de l'incident, **taille estimée**, **fiabilité** de détection.

Cliquer une ligne sélectionne la cible et ouvre sa fiche. **Masquer incidents clos** (coché par défaut) retire de la liste et de la carte les cibles dont l'incident est clos.

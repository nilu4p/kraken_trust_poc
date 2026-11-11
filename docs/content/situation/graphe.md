# Graphe d'entités (analyse de liens)

<p class="where">Onglet <strong>Graphe</strong> du bandeau — tous les rôles. Accès direct : « Explorer les liens » dans un dossier d'incident, une fiche navire AIS ou une fiche cible radar.</p>

Le graphe répond à : **à quoi cette entité est-elle reliée, depuis quand, et pourquoi ?** Un navire n'est plus une ligne isolée : on voit les incidents qui le concernent, les navires qu'il a côtoyés, les échos radar près desquels il se trouvait, et ses inscriptions sur des listes.

## Entités

| Forme | Entité | Ce qu'elle porte |
| --- | --- | --- |
| ● cercle bleu | **Navire** (AIS / VMS) | nom, MMSI, IMO, pavillon, type, anciens noms (registre), dernière position avant la fin de la fenêtre |
| ■ carré violet | **Incident** | référence, titre, statut et sévérité **à la fin de la fenêtre** (pastille de couleur de sévérité), risque |
| ◆ losange rouge | **Écho radar** | passage Sentinel-1, position, risque et fiabilité, dossier de preuve |
| ▲ triangle gris | **Entrée de la liste de surveillance** | source (liste INN, ajout manuel), motif |
| ⬢ hexagone gris | **Entité sanctionnée** (OpenSanctions) | listes concernées, score, lien vers la fiche |

## Liens

Chaque lien a une **date de début et de fin** et ses **preuves** (cliquer le lien) :

| Trait | Lien | Établi quand |
| --- | --- | --- |
| plein | navire — incident | le navire est l'objet de l'incident, ou le second navire d'un rendez-vous en mer |
| plein | incident — écho radar | l'écho est une observation de l'incident |
| tirets | navire — navire : **proximité AIS** | à moins de **1 km** l'un de l'autre dans le même créneau de 10 min, **au moins deux fois** (≥ 20 min) — le lien donne la durée et la distance minimale |
| pointillés | écho radar — navire : **navire proche au passage** | navire AIS à moins de **10 km** de l'écho au moment exact du passage satellite (position projetée) : escorte, navire-mère, partenaire de transbordement… |
| mixte | navire — liste / sanction | correspondance (MMSI, IMO, indicatif ou nom ; score OpenSanctions) |

## Utilisation

1. **Point de départ** : rechercher un navire (nom, MMSI, IMO, indicatif) ou un incident (KRK-…), ou arriver depuis un dossier, une fiche navire ou une cible radar.
2. **Fenêtre** : 24 h, 7 j, 30 j ou 90 j, se terminant **maintenant** ou à un **instant passé** (« jusqu'au … », en UTC). Seul ce qui était connu à la fin de la fenêtre est utilisé : incidents ouverts plus tard, positions postérieures et dossiers scellés plus tard n'apparaissent pas.
3. **Profondeur** : *liens directs*, ou *liens des liens* (le voisinage des voisins).
4. **Étendre** : cliquer une entité puis « Étendre depuis ce nœud » ajoute son voisinage. Les entités déjà placées ne bougent pas.
5. **Lecture dans le temps** : la barre du bas fait défiler la fenêtre ; les liens **apparaissent quand ils se sont produits** (▶ pour une lecture animée).
6. **Liste des liens** : la même information en tableau chronologique (date, entités, nature du lien, preuves) — pratique pour un compte rendu.
7. Molette pour zoomer, glisser le fond pour déplacer ; les cases de la légende masquent un type de lien.

!!! tip "Exemple de lecture"
    Un écho radar sans AIS relié, au moment du passage, à un chalutier étranger situé à 2 km, lui-même relié par une proximité AIS de 40 min à un cargo inscrit sur la liste INN : schéma typique d'un transbordement. Chaque lien porte la preuve (distance, durée, heure) à reporter dans le dossier.

!!! info "Limites"
    150 entités au plus par graphe (réduire la fenêtre ou la profondeur). La proximité AIS ne voit que les navires qui émettent ; un navire sans AIS n'apparaît que par les échos radar. Les rendez-vous enregistrés avant cette version ne relient que le premier navire à l'incident.

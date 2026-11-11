# Section Règles : comprendre, régler, créer

<p class="where">Onglet <strong>Règles</strong> du bandeau — lecture pour tous ; activation, création et modification : superviseur et plus ; test : analyste et plus.</p>

Une **règle** surveille en continu l'image maritime (AIS, VMS, radar, renseignement) et ouvre un incident quand **toutes** ses conditions sont réunies. La section Règles rassemble au même endroit ce que fait chaque règle, pourquoi, avec quels réglages, à partir de quelles sources, et ce qu'elle a donné.

## Règles intégrées

Une fiche par règle, regroupées par famille (Radar, Présence, Comportement, Renseignement) :

| Élément de la fiche | Contenu |
| --- | --- |
| **État** | *active*, *en attente de source* (clé manquante, nommée dans la fiche) ou *désactivée* |
| **Résumé** | ce que la règle cherche, en une phrase |
| **Bilan 30 j** | incidents ouverts, clos, et **part de fausses alarmes** dans les clôtures (vert < 25 %, orange < 50 %, rouge au-delà), date du dernier déclenchement |
| **Comprendre** | *pourquoi* la règle existe, *quand* elle se déclenche (chaque condition), son *effet* (type d'incident, risque, canal), ses *limites connues* |
| **Paramètres de réglage** | valeur actuelle, valeur par défaut, explication et effet d'une hausse (▲) ou d'une baisse (▼) — modifiables par un administrateur dans [Paramètres](../administration/parametres.md) |
| **Désactiver / Activer** | superviseur ; tracé dans l'audit (`rules.toggle`). La règle radar (cœur de la détection) ne se désactive pas |

!!! tip "Lire le bilan"
    Une règle à plus de 50 % de fausses alarmes use l'attention des opérateurs : ajustez ses paramètres, ou créez une règle sur mesure plus ciblée et désactivez l'intégrée.

## Règles sur mesure

Composées **sans programmation**, à partir d'un catalogue fermé de critères. Rien de ce qui est saisi n'est exécuté : chaque règle est vérifiée (critères, opérateurs, valeurs, bornes) avant d'être enregistrée.

### Les critères disponibles

| Critère | Valeurs | Exemple |
| --- | --- | --- |
| Zone maritime | parmi / hors de : mer territoriale, zone contiguë, ZEE, hors eaux françaises | en mer territoriale |
| Pavillon étranger | oui / non | sous pavillon étranger |
| Pavillon | parmi / hors d'une liste de pays | Venezuela, Guyana, Suriname |
| Type de navire (AIS) | pêche, cargo, pétrolier, passagers, plaisance, remorquage, autorités, non déclaré | type non déclaré |
| Navire de pêche | oui / non | (type AIS 30 ou engin connu de GFW) |
| Vitesse | inférieure / supérieure à N nœuds | moins de 5 nœuds (vitesse de pêche) |
| Silence AIS depuis | moins / plus de N heures | plus de 6 h |
| Dans une aire marine protégée | oui / non | |
| Zone de surveillance (dessinée) | dans / hors d'une ou plusieurs [zones dessinées sur la carte](../situation/zones.md) | « Estuaire du Maroni » |
| Zone personnalisée (cercle) | à l'intérieur / à l'extérieur d'un cercle (centre + rayon en km) | 15 km autour de l'estuaire du Maroni |
| De nuit | oui / non (heure solaire locale 18 h 30 – 6 h) | |
| Sous licence | oui / non (VMS ou liste des navires autorisés) | sans licence |
| Sur la liste de surveillance | oui / non | |
| Sous sanctions | oui / non (criblage OpenSanctions) | |
| Nom du navire | contient un texte | « SIN NOMBRE » |

Les conditions se rangent en **groupes** : dans un groupe, toutes doivent être remplies (« et ») ; il suffit qu'**un** groupe le soit (« ou »). Jusqu'à **5 groupes** de **10 conditions**. Exemple : *« Si un navire (est un navire de pêche et navigue à moins de 5 nœuds) ou (est sur la liste de surveillance), alors… »*. Bouton **Ajouter un groupe « ou »** dans le constructeur. Le test et « Pourquoi cette alerte ? » montrent le groupe qui a déclenché la règle.

### Ce que l'on règle ensuite

| Réglage | Effet |
| --- | --- |
| **Risque** (1–100) | détermine la sévérité de l'incident : P1 ≥ 75, P2 ≥ 55, P3 ≥ 35, sinon P4 |
| **Notification** | `#alertes` (opérateurs de quart) ou `#surveillance` (suivi) |
| **Au plus une alerte par navire toutes les…** | 1 h à 7 jours : évite de réveiller le quart dix fois pour le même navire |

La règle s'affiche **comme une phrase** : *« Si un navire est un navire de pêche et est sous pavillon étranger et navigue à moins de 5 nœuds et se trouve en : mer territoriale, alors ouvrir un incident P1 (risque 80) signalé sur #alertes, au plus une fois par navire toutes les 12 h. »*

### Tester avant d'activer

Le bouton **Tester** applique la règle **sans rien créer** :

- **maintenant**, ou **à un instant passé** (rejeu, jusqu'à 90 jours) pour vérifier qu'elle aurait vu un cas connu ;
- liste des navires détectés, avec **chaque condition et la valeur observée** (vitesse, pavillon, distance au centre du cercle, heure solaire…) ;
- **presque détectés** : navires écartés par la dernière condition seulement, avec la valeur qui les a écartés — utile pour ajuster un seuil.

**Modèles** proposés pour démarrer : pêcheur étranger lent en mer territoriale, présence dans une zone sensible (cercle), pêcheur silencieux depuis 6 h.

### Fonctionnement

- Évaluée **chaque minute** avec les règles intégrées, sur les navires entendus dans la dernière heure (48 h si la règle porte sur le silence AIS).
- Incident de type « Règle sur mesure », rattaché au navire, avec l'explication complète.
- Création, modification, activation et suppression tracées dans l'audit (`rules.create`, `rules.update`, `rules.delete`). Supprimer une règle ne supprime pas les incidents qu'elle a ouverts.

## « Pourquoi cette alerte ? »

Chaque incident ouvert par une règle conserve, **tel qu'il a été décidé** : la règle déclenchée, chaque condition avec la **valeur observée**, les **paramètres en vigueur** à ce moment-là et, pour le radar, les facteurs du score de risque. Il s'affiche dans le [dossier d'incident](../incidents/dossier.md#pourquoi-cette-alerte). Une condition marquée ⚠ est une **réserve** (par exemple : collecte AIS interrompue au passage du satellite) — l'alerte existe, mais ce point est à vérifier.

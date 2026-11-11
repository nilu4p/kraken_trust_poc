# Rapport d'activité de la ZEE

<p class="where">Vue Situation › panneau latéral › onglet <strong>Activité</strong>.</p>

Bilan de la **pêche apparente** dans toute la ZEE de Guyane sur une période, sur le modèle du rapport de zone de la carte Global Fishing Watch. Il répond à des questions de pilotage : qui pêche dans la ZEE, combien, avec quels engins, à quel rythme, et où ces navires débarquent.

!!! note "Ce que mesure le rapport"
    Ce sont des **heures de pêche apparente déduites de l'AIS** par GFW (comportement de la trace : vitesse, changements de cap). Un navire qui n'émet pas d'AIS n'y figure pas : la pêche non déclarée se cherche avec les [détections SAR](legende.md#detections-sar-global-fishing-watch) et les scans radar. Délai de publication GFW : environ 4 jours.

## Période

- **30 j, 3 mois** (défaut), **6 mois, 12 mois** : période qui se termine au jour de l'instant de référence (en [rejeu](chronologie.md), au jour rejoué).
- **Dates libres** : deux champs date ; 366 jours au plus (limite de GFW).
- Jusqu'à 120 jours, la courbe est **journalière** (regroupée par semaine au-delà de 45 jours) ; au-delà, **mensuelle**.

Le premier calcul d'une période interroge GFW (trois rapports successifs, jusqu'à une minute) ; il est ensuite en cache 12 heures.

## Contenu

| Bloc | Contenu |
| --- | --- |
| **Indicateurs** | heures de pêche AIS, part étrangère (hors France et outre-mer), nombre de navires dont étrangers |
| **Courbe** | heures par jour, semaine ou mois, empilées : pavillon étranger (orange, en bas), français (bleu), inconnu (gris) ; survol = détail de la barre |
| **Pavillons** | les 8 premiers pavillons, heures et part du total |
| **Engins** | type d'engin estimé par GFW (fileyeurs, chalutiers, palangriers…) |
| **Navires les plus actifs** | 10 premiers (jusqu'à 50) : nom, pavillon, engin, MMSI, heures ; marqués **liste INN** (rouge), **licence** (liste des navires autorisés ou VMS) ou **étranger**. Un clic ouvre la [fiche fusion](fiche-navire.md) du navire s'il est connu de l'historique AIS |
| **Escales dans les ports de la zone** | escales (navire arrêté au port, confiance GFW 3 ou 4) par port, nombre de navires, escales de navires étrangers |

- **Afficher l'effort de pêche sur la carte** active la couche *Effort de pêche AIS GFW* (orange = étranger, bleu = français ; période réglée dans l'onglet Couches).
- **Ouvrir sur la carte GFW** ouvre le même rapport sur globalfishingwatch.org (site externe) pour comparaison.

Sans token GFW (Administration › Clés & intégrations), le rapport affiche « GFW_TOKEN non configuré ».

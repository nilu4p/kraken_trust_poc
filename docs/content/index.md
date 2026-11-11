# Base de connaissances KRAKEN-Trust

KRAKEN-Trust surveille la **ZEE de Guyane** contre la pêche INN. La plateforme croise des images **radar satellite** (Sentinel-1), les positions **AIS** et **VMS**, le renseignement sur les navires (Global Fishing Watch, OpenSanctions, liste INN) et le contexte océanographique. Elle transforme les anomalies en **incidents** traités comme dans un SOC : détecter → qualifier → escalader → intervenir → clore. Chaque preuve est **scellée** (C2PA signé par la PKI de l'instance) et sa **chaîne de possession** tracée, pour pouvoir être produite et vérifiée par un tiers.

Cette base explique **tout ce que vous voyez** dans l'application : chaque symbole, chaque menu, chaque option, chaque calcul. Les termes techniques soulignés en pointillés affichent leur définition au survol ; ils sont tous repris dans le [glossaire](glossaire.md).

!!! tip "Trouver une réponse vite"
    Utilisez la **recherche** (en haut, ou touche ++s++ / ++slash++) : elle cherche dans toutes les pages, y compris les noms de paramètres (`cfar_k`), les libellés de la légende et les codes (`P1`, `MMSI`…).

## Par où commencer ?

<div class="grid cards" markdown>

-   :material-map-marker-radius: **Je regarde la carte**

    ---
    Que veut dire ce point rouge qui clignote ? Cette flèche orange ?

    [:octicons-arrow-right-24: Légende de la carte](situation/legende.md)

-   :material-alarm-light: **Je traite un incident**

    ---
    Statuts, qui peut faire quoi, délais, escalade vers l'AEM.

    [:octicons-arrow-right-24: Cycle de vie d'un incident](incidents/cycle-de-vie.md)

-   :material-calculator-variant: **Je veux comprendre un score**

    ---
    D'où viennent le score de risque /100 et l'indice de fiabilité ?

    [:octicons-arrow-right-24: Score de risque](moteur/risque.md) · [Fiabilité](moteur/fiabilite.md)

-   :material-clipboard-check: **Je dois faire quelque chose de précis**

    ---
    Prendre un quart, investiguer une cible, préparer une patrouille, monter un dossier judiciaire.

    [:octicons-arrow-right-24: Guides pratiques](guides/index.md)

-   :material-compass: **Je dois décider ou rendre compte**

    ---
    Quel moyen peut intercepter, avant quand, dans quelles eaux ? Compte rendu et SITREP signés.

    [:octicons-arrow-right-24: Commandement](commandement/index.md)

-   :material-rocket-launch: **J'installe la plateforme**

    ---
    Prérequis, installation, clés des services, vérifications, mise en production.

    [:octicons-arrow-right-24: Mise en service](mise-en-service/index.md)

-   :material-cog: **J'administre la plateforme**

    ---
    Comptes, clés d'API, paramètres opérationnels, imports.

    [:octicons-arrow-right-24: Administration](administration/index.md)

</div>

## Organisation de cette documentation

| Rubrique | Contenu |
| --- | --- |
| [Prise en main](prise-en-main/index.md) | L'interface générale, l'[index des écrans](prise-en-main/index-ecrans.md), les rôles, un parcours d'opérateur, l'[installation](mise-en-service/index.md) et l'[obtention des clés](mise-en-service/comptes-externes.md) |
| [Guides pratiques](guides/index.md) | Procédures pas à pas : quart, investigation, enquête navire, notification, patrouille, dossier judiciaire, compte rendu au commandement, réglages, démonstration |
| [Situation](situation/index.md) | La carte : légende symbole par symbole, panneau latéral, fiche cible, fiche navire, dossier du point, scan |
| [Incidents](incidents/index.md) | La file de traitement, les statuts, les sévérités P1-P4, les SLA, les types d'incident, le dossier |
| [Commandement](commandement/index.md) | Aide à la décision (cible maintenant, interception par moyen, options, cadre juridique), moyens d'intervention, compte rendu d'incident et SITREP signés |
| [Preuves](preuves.md) · [Audit](audit.md) | Coffre de preuves scellées C2PA, chaîne de possession, journal d'audit infalsifiable |
| [Administration](administration/index.md) | Utilisateurs, clés, paramètres (avec leurs effets), liste de surveillance, catalogue, import VMS, santé |
| [Moteur et calculs](moteur/index.md) | Les algorithmes et formules exacts : CFAR, fiabilité, corrélation AIS, risque, règles 24/7, fusion des sources, corroboration multi-capteurs, détection optique et nocturne |
| [Sources](sources.md) · [Notifications](notifications.md) · [Sécurité](securite.md) | D'où viennent les données, où partent les alertes, comment la plateforme est protégée |
| [Exploitation](exploitation.md) · [Configuration](mise-en-service/configuration.md) · [Données](donnees.md) · [API](api.md) | Commandes `make`, variables d'environnement, conservation des données, intégration par API |
| [FAQ](faq.md) · [Glossaire](glossaire.md) | Problèmes fréquents et messages d'erreur, tous les termes de A à Z |

## Les deux questions que pose chaque cible

La plateforme sépare toujours deux notions qu'il ne faut pas confondre :

| | **Fiabilité de détection** (0 – 100 %) | **Score de risque** (0 – 100) |
| --- | --- | --- |
| Question | *Est-ce vraiment un navire ?* | *Ce navire est-il suspect ?* |
| Repose sur | qualité du signal radar, forme, double polarisation, bathymétrie, état de la mer, historique | zone légale, absence d'AIS, comportements GFW, liste INN, aire protégée, front thermique, taille… |
| Effet | sous le seuil (25 % par défaut) la cible est écartée | fixe la **sévérité** P1 à P4 de l'incident |
| Détail | [Indice de fiabilité](moteur/fiabilite.md) | [Score de risque](moteur/risque.md) |

Une cible peut être très fiable (c'est sûrement un navire) mais peu risquée (petit navire loin des côtes), ou l'inverse.

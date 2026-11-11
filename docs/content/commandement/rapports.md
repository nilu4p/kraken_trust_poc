# Rapports à la chaîne de commandement

<p class="where">Compte rendu d'incident : bouton <strong>Compte rendu</strong> du dossier d'incident. SITREP : onglet <strong>Commandement</strong> → <strong>SITREP</strong>. Registre : <strong>Commandement</strong> → <strong>Rapports émis</strong>.</p>

Deux rapports, rédigés **à partir des enregistrements de la plateforme** et complétés par l'opérateur :

| Rapport | Référence | Rédaction | Émission |
| --- | --- | --- | --- |
| **Compte rendu d'incident** | `CR-année-numéro` | Analyste | Analyste |
| **SITREP** (rapport de situation sur une période) | `SITREP-année-numéro` | Analyste | **Superviseur** |

## Rédiger, prévisualiser, émettre

1. Renseigner les **destinataires** (suggestions : CROSS Antilles-Guyane, Préfet de Guyane (AEM), COMSUP FAG, DM Guyane, Parquet de Cayenne), la **mention de protection**, l'**appréciation** (ce que l'on comprend) et les **propositions et demandes** (ce que l'on demande au commandement).
2. **Prévisualiser** : le projet est recalculé à partir des données du moment. Rien n'est enregistré, la mention « Projet — non émis » est affichée.
3. **Émettre et signer** (confirmation demandée) : le texte est **figé**, son empreinte SHA-256 calculée, il est **signé** (ECDSA) avec le certificat de l'instance qui scelle déjà les preuves, enregistré au registre, tracé dans l'audit (`report.issue`) et, pour un compte rendu, inscrit dans la chronologie de l'incident.

Un rapport émis ne se modifie plus : pour corriger, émettez un nouveau rapport qui le remplace.

## Format

Format message, lisible tel quel dans une messagerie opérationnelle :

```text
DIFFUSION RESTREINTE
------------------------------------------------------------------
COMPTE RENDU D'INCIDENT
RÉF.          : CR-2026-0007
GDH           : 221430Z OCT 26
ORIGINE       : Centre de surveillance maritime KRAKEN — Guyane
RÉDACTEUR     : Chef de quart (chef)
DESTINATAIRES : CROSS Antilles-Guyane ; Préfet de Guyane (AEM)
OBJET         : KRK-2026-0012 — Navire sans AIS en ZEE (~42 m) (P1)
------------------------------------------------------------------

1. SITUATION
   ...
```

Le **GDH** (groupe date-heure) suit la forme OTAN `JJHHMMZ MOI AA`, en UTC. L'émetteur (`REPORT_ORIGINATOR`) et la mention par défaut (`REPORT_CLASSIFICATION`, *DIFFUSION RESTREINTE* par défaut ; *NON PROTÉGÉ* toujours proposé) se règlent dans la [configuration](../mise-en-service/configuration.md).

!!! warning "Mention de protection"
    La mention est un marquage du document : elle ne protège rien par elle-même. Diffusez le rapport par les canaux habilités pour ce niveau.

## Contenu du compte rendu d'incident

| § | Contenu | Source |
| --- | --- | --- |
| 1 | **Situation** : référence, nature, sévérité, statut, position (DMS), zone, dates, affectation, autorité saisie | dossier d'incident |
| 2 | **Détection et évaluation** : risque, fiabilité, longueur, scène radar, facteurs de risque et signaux de fiabilité, règle déclenchée et conditions vérifiées | preuve scellée, « Pourquoi cette alerte ? » |
| 3 | **Identification** : MMSI, nom, pavillon — ou « non identifié » | AIS |
| 4 | **Situation actuelle et aide à la décision** : position estimée et incertitude, projection, option recommandée et motif, tableau des moyens, hypothèses | [Aide à la décision](decision.md), figée à l'émission |
| 5 | **Chronologie des actions** | chronologie de l'incident |
| 6 | **Vérifications du playbook** réalisées / restant à faire | playbook |
| 7 | **Preuves scellées** : dossier, cible, date, intégrité vérifiée, SHA-256 de chaque vignette | coffre de preuves |
| 8 | **Cadre juridique** | aide à la décision |
| 9-10 | **Appréciation**, **Propositions et demandes** | opérateur |

## SITREP

Période au choix (24 h par défaut, 31 jours maximum, saisie en UTC). Contenu :

| § | Contenu |
| --- | --- |
| 1 | **Situation générale** en une phrase |
| 2 | **Incidents** : ouverts sur la période par sévérité, clos par résolution, ouverts en fin de période, hors délai, non affectés, délais moyens de prise en compte et de clôture (30 j) |
| 3 | **Incidents marquants** P1/P2 actifs sur la période |
| 4 | **Action de l'État en mer** : saisines, interventions, annulations, clôtures |
| 5 | **Capteurs et couverture** : scènes radar traitées, détections, navires AIS entendus, couverture de la collecte AIS, interruptions ≥ 30 min, sources dégradées |
| 6 | **Moyens** et leur disponibilité |
| 7-8 | **Appréciation**, **Propositions et demandes** |

## Diffuser et vérifier

| Action | Effet |
| --- | --- |
| **Imprimer / PDF** | imprime le rapport seul, en mise en page papier A4 (« Enregistrer au format PDF » dans la boîte d'impression du navigateur) |
| **Message signé (.txt)** | le texte signé, suivi d'un bloc de scellement : empreinte SHA-256, signature ECDSA (base64), certificat. Le texte signé est **exactement** tout ce qui précède la ligne de `=` (tracé : `report.export`) |
| **Copier le texte** | pour coller le message dans une messagerie |
| **Vérifier l'intégrité** | recalcule l'empreinte, vérifie la signature avec le certificat enregistré et que ce certificat a été émis par la racine de l'instance (y compris une racine archivée après renouvellement) ; résultat tracé (`report.verify`) |

Un destinataire peut vérifier hors plateforme : SHA-256 du texte signé, puis vérification ECDSA-SHA256 de la signature avec la clé publique du certificat joint, et empreinte de la racine comparée à celle publiée par le centre ([Preuves](../preuves.md)).

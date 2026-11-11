# Dossier d'incident

<p class="where">Incidents → clic sur une ligne. Ou lien depuis la fiche cible, une notification, la fiche navire.</p>

## Synthèse

| Champ | Contenu |
| --- | --- |
| Référence, titre, type | `KRK-2026-0042`, description générée, type d'incident |
| **Statut** et **Sévérité** | voir [cycle de vie](cycle-de-vie.md) et [sévérités](severite-sla.md) |
| **Risque** | score maximal observé /100 |
| **Fiabilité détection** | indice de la meilleure observation (incidents radar) |
| **Première / Dernière détection** | heures UTC de la première et de la plus récente observation |
| **Délai de détection** | temps entre l'observation (passage satellite, signal AIS) et l'ouverture de l'incident. Au-delà de 6 h : « image ancienne : situation à revérifier » — le navire a pu partir |
| **Échéance triage / résolution** | dates limites SLA (rouge si dépassées) |
| **Assigné** | opérateur responsable ; un superviseur peut le changer dans la liste déroulante, un analyste peut se l'attribuer |
| **Escaladé à** | autorité saisie et sa référence |
| **Résolution** | motif de clôture et note |
| **Voir la situation tactique** | revient sur la carte |

## Pourquoi cette alerte ?

Le raisonnement enregistré à l'ouverture de l'incident, **tel qu'il a été décidé** (il ne change pas si les paramètres sont modifiés ensuite) :

| Élément | Contenu |
| --- | --- |
| Règle déclenchée | nom (lien vers l'onglet **Règles**), heure de la décision |
| Conditions | chaque condition, ✅ remplie avec la **valeur observée** (pavillon, vitesse, zone, durée de silence…) ou ⚠ **réserve** à vérifier |
| Facteurs du score | pour une cible radar : ce qui a fait monter ou baisser le risque |
| Paramètres en vigueur | seuils appliqués à ce moment (ex. `cfar_k = 5`, `ais_gap_hours = 4`) |

Incidents ouverts avant cette fonctionnalité : « explication non enregistrée ». Voir [Section Règles](../moteur/regles-page.md).

## Observations

Panneau **Observations · n** : une carte par détection rattachée à l'incident.

| Élément | Contenu |
| --- | --- |
| Vignette | extrait radar de la cible (incident radar), ou mention **Détection AIS** (règle 24/7) |
| Identifiant · risque | identifiant de la cible (`SAR-002`) ou du navire (`AIS-710123456`) et score de cette observation |
| Date, coordonnées | heure UTC et position de la détection |
| **Dossier de preuves** | ouvre le dossier scellé correspondant dans l'onglet Preuves |

Plusieurs observations = le même objet a été revu (nouveau passage, nouvelle occurrence de la règle) : la sévérité ne fait que monter.

## Affectation

- **Superviseur** : liste déroulante à droite des boutons — choisir un opérateur ou *Non assigné*.
- **Analyste** : bouton **Me l'attribuer** (impossible d'affecter quelqu'un d'autre).
- Chaque changement est inscrit dans la chronologie (« Affectation »).

## Boutons d'action

Affichés selon le statut et votre rôle :

| Bouton | Disponible quand | Effet |
| --- | --- | --- |
| **Prendre en charge** | Nouveau · analyste | → En triage |
| **Qualifier (navire suspect)** | En triage · analyste | → Qualifié |
| **Escalader** / **Mettre à jour l'escalade** | Qualifié, Escaladé, Intervention · superviseur | Formulaire autorité + référence ; → Escaladé ; coche l'étape du playbook ; notifie #escalades |
| **Intervention engagée** | Qualifié ou Escaladé · superviseur | → Intervention en cours |
| **Annuler l'escalade** | Escaladé · superviseur | Motif obligatoire ; → Qualifié ; autorité et référence retirées du dossier (gardées dans la chronologie) ; étape « Transmettre » décochée |
| **Annuler l'intervention** | Intervention en cours · superviseur | Motif obligatoire ; → Escaladé si une escalade existe (conservée), sinon → Qualifié ; étape « Suivre l'intervention » décochée |
| **Clore** | voir [règles de clôture](cycle-de-vie.md#transitions-autorisees) | Formulaire : résolution (seules celles permises à votre rôle sont proposées) + note |
| **Rouvrir** | Clos · superviseur | → En triage |
| **Compte rendu** | Toujours · analyste | Projet de compte rendu d'incident pour la chaîne de commandement, puis émission signée — voir [Rapports](../commandement/rapports.md) |

Le panneau **Aide à la décision** (incidents non clos) estime la position actuelle de la cible, le délai de chaque moyen pour être sur zone et l'option d'action recommandée — voir [Aide à la décision](../commandement/decision.md). **Voir la situation tactique** ouvre la carte sur la dernière détection radar de l'incident.

## Formulaire d'escalade

| Champ | Contenu |
| --- | --- |
| **Autorité saisie (Action de l'État en Mer)** | liste ci-dessous |
| **Référence de transmission** | numéro de message, d'affaire ou de procédure de l'autorité (120 caractères) |
| **Éléments transmis** | ce qui a été envoyé (position, images, dossier exporté…) |

**Mettre à jour l'escalade** permet de changer d'autorité ou de compléter la référence ; chaque mise à jour est tracée.

## Autorités d'escalade

| Choix | Autorité |
| --- | --- |
| CROSS Antilles-Guyane | Coordination opérationnelle de la surveillance et du sauvetage |
| Préfet — Délégué du gouvernement pour l'AEM | Autorité qui ordonne l'action de l'État en mer en Guyane |
| Marine nationale | Commandement de la zone maritime (moyens hauturiers) |
| Direction de la Mer de Guyane | Affaires maritimes, contrôle des pêches |
| Gendarmerie maritime | Police en mer, constatation d'infractions |
| Douanes — garde-côtes | Contrôles, moyens nautiques et aériens |
| Parquet de Cayenne | Procédure judiciaire |

## Playbook

Panneau **Playbook · n/8** : liste de contrôle cochable par les analystes et au-delà (non modifiable une fois l'incident clos). Une étape cochée est barrée et affiche qui l'a cochée ; recliquer la décoche. Tout est enregistré dans la chronologie.

1. Vérifier la vignette SAR et la fiabilité de détection
2. Contrôler l'historique AIS et les coupures (GFW)
3. Rechercher une imagerie optique (Pléiades / SPOT / Sentinel-2)
4. Vérifier les lumières nocturnes VIIRS
5. Identifier le type de navire, le pavillon, l'activité
6. Transmettre à l'autorité compétente (AEM) — cochée automatiquement par l'escalade
7. Suivre l'intervention (moyen nautique / aérien)
8. Exporter le dossier de preuves scellé pour la procédure

## Chronologie

Journal horodaté de tout ce qui arrive à l'incident, du plus ancien au plus récent : date, **auteur** (`system` pour les automatismes) et type d'événement. Le champ **Ajouter une note d'analyse…** (4 000 caractères) puis **Publier** ajoute un commentaire (analyste et plus, incident non clos).

| Événement | Contenu |
| --- | --- |
| Incident créé par la détection | cible, alerte, risque initial |
| Nouvelle observation corrélée | nouvelle détection rattachée |
| Sévérité relevée | ancienne → nouvelle sévérité |
| Changement de statut | de → vers, note, résolution |
| Affectation | nouvel opérateur |
| Commentaire | note d'analyse (champ « Ajouter une note d'analyse… ») |
| Escalade | autorité, référence |
| Playbook | étape cochée / décochée |
| SLA dépassé | échéance concernée |
| Export du dossier de preuves | dossier exporté |

## Corrélation des détections

Une nouvelle **détection radar** rejoint un incident existant si :

- même type d'incident ;
- incident **ouvert** dont la dernière détection date de moins de **72 h** (`incident_correlation_hours`) ;
- distance de moins de **15 km** (`incident_correlation_km`) — le plus proche est choisi.

Sinon un nouvel incident est créé. Les incidents issus de l'AIS sont corrélés **par navire** (même MMSI), quelle que soit la distance.

**Nouvelle analyse d'une même scène** : un écho déjà rattaché depuis cette scène (à moins de 250 m) n'ajoute **pas** de nouvelle observation et **n'est pas rescellé** ; seules les cibles nouvelles (par exemple après un changement de réglage) produisent un dossier de preuves. Le message du scan l'indique (« scène déjà analysée : cibles connues non dupliquées »).

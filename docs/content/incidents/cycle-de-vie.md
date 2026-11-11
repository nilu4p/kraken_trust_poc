# Cycle de vie d'un incident

Inspiré des centres opérationnels de cybersécurité (SOC) : **détecter → trier → qualifier → escalader → intervenir → clore**.

```text
 Nouveau ──▶ En triage ──▶ Qualifié ──▶ Escaladé ──▶ Intervention ──▶ Clos
    │            │            │  └──────────────────────▶│              ▲
    └────────────┴────────────┴─────── Clore ────────────┴──────────────┘
                                   Clos ──▶ En triage (réouverture)
```

## Les statuts

| Statut | Signification | Qui fait avancer |
| --- | --- | --- |
| **Nouveau** | Créé automatiquement par une détection ou une règle ; personne ne l'a encore regardé. Le délai de **triage** court | — |
| **En triage** | Un opérateur l'a pris en charge et l'analyse. L'heure de prise en charge arrête le délai de triage ; le délai de **résolution** court | Analyste : *Prendre en charge* |
| **Qualifié** | L'analyse conclut à un **navire suspect** qui mérite une suite | Analyste : *Qualifier (navire suspect)* |
| **Escaladé** | Transmis à une autorité de l'Action de l'État en mer, avec sa référence | Superviseur : *Escalader* |
| **Intervention en cours** | Un moyen nautique ou aérien est engagé | Superviseur : *Intervention engagée* |
| **Clos** | Traitement terminé, avec une **résolution** obligatoire | Voir ci-dessous |

## Transitions autorisées

| De → Vers | Rôle minimum |
| --- | --- |
| Nouveau → En triage | Analyste |
| En triage → Qualifié | Analyste |
| Qualifié → Escaladé | Superviseur |
| Qualifié → Intervention | Superviseur |
| Escaladé → Intervention | Superviseur |
| Clos → En triage (*Rouvrir*) | Superviseur |
| Escaladé → Qualifié (*Annuler l'escalade*) | Superviseur, **motif obligatoire** |
| Intervention → Escaladé (*Annuler l'intervention*, escalade conservée) | Superviseur, **motif obligatoire** |
| Intervention → Qualifié (*Annuler l'intervention*, sans escalade) | Superviseur, **motif obligatoire** |
| Toute étape ouverte → Clos | Analyste **si** Nouveau ou En triage **et** résolution « Activité légitime », « Fausse alarme » ou « Doublon » ; sinon Superviseur |

**Annulations.** Une escalade retirée efface l'autorité et la référence du dossier (elles restent lisibles dans la chronologie : « Escalade annulée … escalade retirée (CROSS Antilles-Guyane, réf. …) — motif : … ») et décoche l'étape « Transmettre à l'autorité » du playbook. Une intervention annulée revient à *Escaladé* si une escalade existe (elle est conservée), sinon à *Qualifié*, et décoche « Suivre l'intervention ». Chaque annulation est tracée dans l'audit (`incident.cancel`, avec le motif) ; le [rejeu](../situation/chronologie.md) montre l'incident escaladé ou en intervention aux instants antérieurs à l'annulation.

Toute autre transition est refusée par le serveur (« Transition interdite »). Chaque changement est inscrit dans la **chronologie** de l'incident et dans l'**audit**.

## Résolutions (motif de clôture)

| Résolution | Quand l'utiliser | Effet sur la plateforme |
| --- | --- | --- |
| **Pêche INN confirmée** | L'infraction est établie | Statistiques ; le secteur est marqué « navire confirmé » pour les détections futures |
| **Activité légitime** | Navire identifié et autorisé (patrouille, navire sous licence, recherche…) | — |
| **Fausse alarme (pas de navire)** | L'écho n'était pas un navire (vague, rocher, bouée, artefact) | **Apprentissage** : toute détection future à moins de 250 m perd 0,45 de fiabilité |
| **Doublon** | Le même navire est déjà suivi dans un autre incident | — |
| **Sans suite / non interceptable** | Navire réel et suspect, mais hors de portée des moyens ou disparu | — |

Une **note** de clôture peut être ajoutée (motif, références) ; elle figure dans la chronologie et la notification.

## Réouverture

Un superviseur peut **rouvrir** un incident clos : il revient *En triage*, la résolution est effacée, la réouverture est tracée.

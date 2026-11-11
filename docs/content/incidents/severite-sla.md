# Sévérités, SLA et indicateurs

## Sévérité

La sévérité est **déduite du score de risque** à la création, puis **relevée** si une nouvelle observation plus risquée arrive (elle ne baisse jamais automatiquement).

| Sévérité | Score de risque | Lecture |
| --- | --- | --- |
| <span class="sw sq" style="--c:#ff3b3b"></span> **P1 — Critique** | ≥ 75 | Action immédiate |
| <span class="sw sq" style="--c:#ff8c1a"></span> **P2 — Élevée** | 55 – 74 | Traiter dans la vacation |
| <span class="sw sq" style="--c:#f5c542"></span> **P3 — Moyenne** | 35 – 54 | À analyser |
| <span class="sw sq" style="--c:#94a3b8"></span> **P4 — Faible** | < 35 | Pour information |

Les règles 24/7 fixent directement un score ; par exemple un navire listé INN visible à l'AIS vaut 90, donc P1. Voir [Règles 24/7](../moteur/regles.md).

## SLA (délais)

Deux échéances sont calculées à la création de l'incident :

| Sévérité | Triage (prise en charge) | Résolution (clôture) |
| --- | --- | --- |
| P1 | 30 min | 24 h |
| P2 | 2 h | 3 jours |
| P3 | 8 h | 7 jours |
| P4 | 24 h | 14 jours |

- **Triage dépassé** : l'incident n'a pas été pris en charge (*En triage*) avant l'échéance.
- **Résolution dépassée** : l'incident n'est pas clos avant l'échéance.
- Un dépassement est notifié **une fois** sur le canal **#sla** et inscrit dans la chronologie (« SLA dépassé »).
- Un incident clos en retard reste marqué *clos hors SLA* (statistiques).

## Indicateurs

| Indicateur | Définition |
| --- | --- |
| **Ouverts** | Incidents non clos |
| **Non assignés** | Ouverts sans opérateur responsable |
| **SLA dépassés** | Ouverts dont l'échéance en cours est dépassée |
| **MTTA** (Mean Time To Acknowledge) | Délai moyen entre création et prise en charge, sur les 30 derniers jours |
| **MTTR** (Mean Time To Resolve) | Délai moyen entre création et clôture, sur les incidents clos des 30 derniers jours |
| **Clôtures par résolution (30 j)** | Répartition Pêche INN confirmée / Légitime / Fausse alarme / Doublon / Sans suite. Une part élevée de fausses alarmes invite à relever `cfar_k` ou `min_detection_quality` |
| **Détections (7 j)** | Nombre d'observations rattachées à des incidents sur 7 jours |

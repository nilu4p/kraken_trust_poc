# Réagir à une notification

| Canal | Réaction attendue |
| --- | --- |
| **#alertes** | Ouvrir l'incident (lien dans le message), le prendre en charge dans le délai de triage (P1 : 30 min) |
| **#surveillance** | Navire listé INN ou sanctionné dans la ZEE : prévenir immédiatement le superviseur, prise en charge prioritaire |
| **#sla** | Un incident a dépassé son échéance : le superviseur réaffecte ou fait avancer le traitement |
| **#escalades** | Information : une autorité a été saisie, une intervention est engagée ou un incident est clos |
| **#operations** | Scan terminé ou échoué, source en panne ou rétablie : l'administrateur vérifie *Santé des sources* et les clés |
| **#securite** | Verrouillage de compte, nouvel administrateur, clés modifiées, audit altéré : l'administrateur **vérifie que l'action était attendue** ; sinon, alerte sécurité |

## Lire un message d'incident

```text
🔴 P1 · Navire de pêche sous pavillon étranger dans la ZEE
KRK-2026-0042 — Navire de pêche étranger (Brésil) dans la ZEE : PESCADOR   [lien]
Risque 85/100 · Position 5,512° N 52,430° O · Mer territoriale · Statut Nouveau · Non assigné
Navire PESCADOR · MMSI 710123456 · Pavillon Brésil · Type AIS pêche (30) · Vitesse 3,1 nd
```

La pastille donne la sévérité, la première ligne le motif, puis la référence et le lien, les champs de synthèse et les détails propres au type d'incident.

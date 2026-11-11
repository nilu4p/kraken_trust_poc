# Rôles et droits

Les rôles sont **cumulatifs** : chacun hérite de tous les droits des rôles inférieurs.

**Observateur** < **Analyste** < **Superviseur** < **Administrateur**

| Rôle | Profil type | Résumé |
| --- | --- | --- |
| **Observateur** (`viewer`) | autorité, partenaire, stagiaire | Consulte tout (carte, incidents, preuves) sans rien modifier |
| **Analyste** (`analyst`) | opérateur de quart | Lance des scans, prend en charge et qualifie les incidents, classe les fausses alarmes, vérifie et exporte les preuves |
| **Superviseur** (`supervisor`) | chef de quart | Affecte les incidents, **escalade vers l'AEM**, engage l'intervention, clôt et rouvre, émet le SITREP, tient les moyens à jour, lit l'audit, importe le VMS |
| **Administrateur** (`admin`) | responsable technique | Comptes, clés d'API, paramètres opérationnels, liste INN |

## Matrice détaillée

La même matrice est affichée dans *Administration → Utilisateurs*. Elle reflète exactement les contrôles faits par le serveur : un bouton masqué n'est pas seulement caché, l'action est refusée côté serveur.

| Capacité | Observateur | Analyste | Superviseur | Admin |
| --- | :-: | :-: | :-: | :-: |
| Consulter la carte, les cibles, la météo, l'imagerie | ✅ | ✅ | ✅ | ✅ |
| Lancer un scan Sentinel-1 | — | ✅ | ✅ | ✅ |
| Consulter la file et les dossiers d'incident | ✅ | ✅ | ✅ | ✅ |
| Prendre en charge, qualifier, commenter, cocher le playbook | — | ✅ | ✅ | ✅ |
| S'attribuer un incident | — | ✅ | ✅ | ✅ |
| Classer au triage : fausse alarme, activité légitime, doublon | — | ✅ | ✅ | ✅ |
| Affecter un incident à un autre opérateur | — | — | ✅ | ✅ |
| Escalader vers une autorité AEM | — | — | ✅ | ✅ |
| Engager l'intervention, clore avec n'importe quelle résolution, rouvrir | — | — | ✅ | ✅ |
| Consulter l'aide à la décision, les moyens et le registre des rapports | ✅ | ✅ | ✅ | ✅ |
| Lancer une analyse optique / thermique (Sentinel-2, Landsat) autour d'une cible | — | ✅ | ✅ | ✅ |
| Rédiger et émettre (signer) un compte rendu d'incident ; vérifier, exporter un rapport | — | ✅ | ✅ | ✅ |
| Émettre un SITREP ; gérer les moyens d'intervention et leur disponibilité | — | — | ✅ | ✅ |
| Consulter les dossiers de preuves scellés | ✅ | ✅ | ✅ | ✅ |
| Vérifier l'intégrité et exporter un dossier judiciaire | — | ✅ | ✅ | ✅ |
| Ajouter un navire à la liste de surveillance | — | ✅ | ✅ | ✅ |
| Importer des positions VMS | — | — | ✅ | ✅ |
| Ajouter / retirer un navire autorisé (licence) | — | — | ✅ | ✅ |
| Importer la liste des navires autorisés | — | — | — | ✅ |
| Consulter et vérifier le journal d'audit | — | — | ✅ | ✅ |
| Importer la liste INN, retirer des entrées | — | — | — | ✅ |
| Comptes, rôles, réinitialisation de mot de passe | — | — | — | ✅ |
| Clés d'API et webhook (coffre chiffré) | — | — | — | ✅ |
| Paramètres opérationnels | — | — | — | ✅ |

!!! info "Pourquoi l'analyste ne peut-il pas tout clore ?"
    Un analyste peut fermer seul un incident **au triage** si c'est une fausse alarme, une activité légitime ou un doublon : ce sont des décisions courantes et réversibles. Conclure à une **pêche INN confirmée** ou classer *sans suite* engage le service : c'est réservé au superviseur (principe des quatre yeux).

!!! warning "Changement de rôle"
    Changer le rôle d'un compte ou le désactiver **ferme immédiatement toutes ses sessions** : les nouveaux droits s'appliquent sans attendre.

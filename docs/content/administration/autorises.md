# Navires autorisés (licences)

<p class="where">Administration → <strong>Navires autorisés</strong>. Consultation : tous les rôles (API) · ajout / retrait : superviseur · import : administrateur.</p>

En Guyane, des navires étrangers pêchent **légalement** sous licence (accords de l'UE, licences délivrées par l'État — notamment la flottille vénézuélienne de vivaneau). Sans cette liste, chacun d'eux déclencherait la règle « pêche étrangère » à chaque passage, et leur activité légale ferait monter le risque des cibles radar voisines.

## Effets d'une licence valide

| Élément | Sans licence | Avec licence valide |
| --- | --- | --- |
| Règle **pêche étrangère** | incident P1 / P2 | aucun incident |
| Couleur sur la carte | orange « Pêcheur étranger » | turquoise clair « Pêcheur sous licence » |
| Bulle / fiche navire | — | « Sous licence N° … (jusqu'au …) » |
| Événements GFW du navire dans le **score de risque** des cibles radar | comptés | **ignorés** |
| « Pêcheur étranger à proximité » (+5 au risque) | compté | ignoré |
| Règle **pêche en aire protégée** | s'applique | **s'applique toujours** (une licence ne couvre pas une réserve) |
| Liste INN, sanctions, coupures AIS, rendez-vous | s'appliquent | s'appliquent toujours |

Les navires vus au **VMS** sont traités de la même façon (ils sont, par définition, sous licence).

## Correspondance : identifiants certains uniquement

Un navire est reconnu par son **MMSI**, son **IMO** ou son **indicatif radio** — **jamais par son nom**. Un navire INN peut naviguer sous un nom emprunté : une correspondance par nom pourrait le « blanchir ». Une entrée sans aucun de ces trois identifiants est refusée.

Une licence dont la **date de fin de validité est dépassée** est ignorée (affichée grisée « expirée le … ») : le navire redevient soumis à la règle.

## Importer la liste des licences

Fichier **CSV, XLS ou XLSX** avec une ligne d'en-têtes (français ou anglais) :

| Colonne | En-têtes reconnus | Obligatoire |
| --- | --- | --- |
| Nom | `nom`, `navire`, `name`, `vessel` | non |
| MMSI | `mmsi` | un des trois |
| IMO | `imo`, `omi` | un des trois |
| Indicatif | `indicatif`, `indicatif radio`, `ircs`, `call sign` | un des trois |
| Pavillon | `pavillon`, `flag` | non |
| Licence | `licence`, `license`, `autorisation`, `référence` | non |
| Fin de validité | `fin validité`, `expiration`, `valid until`, `date fin` (AAAA-MM-JJ ou JJ/MM/AAAA) | non (sans limite) |
| Note | `note`, `commentaire`, `remarque` | non |

L'import **remplace** toute la liste. Le compte rendu indique le nombre de navires importés et les lignes rejetées (avec le motif : identifiant manquant, date illisible).

```text
Nom;MMSI;IMO;Indicatif;Pavillon;Licence;Fin validité
ALTAMIRA III;775000001;;YYV1234;VEN;UE-GUF-2026-012;31/12/2026
```

## Ajouter, retirer

- **Ajouter une licence** : formulaire (au moins un identifiant certain).
- **Retirer** (après confirmation) : le navire est de nouveau soumis à la règle « pêche étrangère ».

Chaque ajout, retrait et import est tracé dans l'audit (`authorized.add`, `authorized.remove`, `authorized.import`).

!!! warning "Responsabilité"
    Inscrire un navire ici **supprime des alertes**. Ne l'alimentez qu'à partir de sources officielles (liste des licences de la DM Guyane, de la Commission européenne) et tenez les dates de validité à jour.

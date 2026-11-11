# Catalogue, import VMS et santé des sources

## Catalogue des sources

<p class="where">Administration → <strong>Catalogue des sources</strong> (lecture pour tous les rôles via l'API).</p>

Toutes les sources exploitées ou exploitables, regroupées par catégorie, avec leur usage, leurs conditions d'accès et un statut :

| Statut | Signification |
| --- | --- |
| **Active** | Connecteur en place et opérationnel (clé présente ou source libre) |
| **Clé requise** | Connecteur en place, il manque la clé (voir [Clés & intégrations](cles.md)) |
| **Branchable** | Source identifiée mais pas connectée : accès restreint, payant, ou redondant (procédure d'accès via le lien) |

Le détail de chaque source est dans [Sources de données](../sources.md).

## Import VMS

<p class="where">En haut du Catalogue des sources — superviseurs et administrateurs.</p>

Le **VMS** est la balise satellitaire obligatoire des navires de pêche sous licence. Ces données sont réservées à l'administration (CNSP, DM Guyane) : il n'existe pas d'API publique. L'autorité fournit un export, importé ici.

**Formats acceptés** (10 Mo maximum) :

=== "NAF"

    Messages standard `//SR// … //ER//`, un ou plusieurs par fichier :

    ```text
    //SR//AD/FRA//FR/VEN//TM/POS//RC/YYV1234//NA/DON PEDRO//FS/VEN//LT/+5.512//LG/-052.430//SP/35//CO/270//DA/20261020//TI/1030//ER//
    ```

    | Code | Champ |
    | --- | --- |
    | `TM` | type de message (`POS`, `ENT`, `EXI`, `MAN` acceptés) |
    | `RC` | indicatif radio · `IR` immatriculation CFR · `NA` nom · `MM` MMSI |
    | `FS` | État du pavillon (code ISO à 3 lettres) |
    | `LT` / `LG` | latitude / longitude en degrés décimaux |
    | `SP` | vitesse en **dixièmes** de nœud (35 = 3,5 nd) · `CO` cap en degrés |
    | `DA` / `TI` | date `AAAAMMJJ` et heure `HHMM` UTC |

=== "CSV"

    Séparateur `,` `;` tabulation ou `|` détecté automatiquement, virgule décimale acceptée. En-têtes reconnus (français ou anglais) :

    | Donnée | En-têtes acceptés |
    | --- | --- |
    | latitude / longitude (**obligatoires**) | `lat`, `latitude` / `lon`, `lng`, `longitude` |
    | date-heure (**obligatoire**) | `ts`, `timestamp`, `datetime`, `date heure`, `horodatage`… ou `date` + `heure` |
    | identifiant (**au moins un**) | `mmsi`, `ircs` / `indicatif radio`, `cfr` / `immatriculation`, `nom navire` |
    | optionnels | `vitesse` / `sog`, `cap` / `cog`, `pavillon` / `flag` |

**Ce que fait l'import** : chaque position rejoint l'historique des navires avec la source `vms`. Un navire sans MMSI est identifié `VMS-<indicatif>`. Ensuite :

- un écho radar situé là où un navire VMS se trouvait au moment du passage est **expliqué** (ce n'est pas un navire sans AIS) ;
- un navire vu au VMS dans les 7 derniers jours est considéré **sous licence** : la règle *pêche étrangère* l'ignore ;
- le panneau Couverture affiche une ligne « VMS importé ».

L'import est tracé dans l'audit (`vms.import`).

## Santé des sources

<p class="where">Administration → <strong>Santé des sources</strong>.</p>

État de chaque service externe **depuis le démarrage** du serveur :

| Information | Signification |
| --- | --- |
| **ok** | dernier appel réussi |
| **dégradé** | 1 ou 2 échecs consécutifs |
| **hors service** | 3 échecs consécutifs ou plus — notifié sur **#operations** (et le retour à la normale aussi) |
| Dernier succès / dernier échec / dernière erreur | horodatages et cause (code HTTP, délai dépassé…) |

*Aucun appel externe depuis le démarrage* : normal juste après un redémarrage.

Le panneau **Surveillance** rappelle l'état de la **veille automatique**, le nombre de **scènes traitées** et la date du **dernier traitement**.

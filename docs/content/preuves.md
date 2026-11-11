# Preuves

<p class="where">Onglet <strong>Preuves</strong> — « Coffre de preuves ».</p>

Chaque scan qui trouve des navires sans AIS produit un **dossier de preuves** scellé (`alerte_AAAAMMJJ_HHMMSS_xxxxxxxx`). Le but : qu'un procès-verbal puisse s'appuyer sur une image satellite dont on peut **démontrer l'origine et l'intégrité**.

## Contenu d'un dossier

| Fichier | Rôle |
| --- | --- |
| `radar_crop.jpg` | **Planche** : extrait Sentinel-1 avec toutes les cibles annotées. Porte un **manifeste C2PA embarqué** signé |
| `chip_SAR-00x.jpg` | **Vignette** de chaque cible ; son empreinte SHA-256 est inscrite dans le manifeste signé |
| `kraken_alerts.geojson` | Données de l'alerte : positions, scores, raisons, sources |
| `manifest.json` | Version lisible du manifeste : assertions (scène, capteur, horodatage, cibles), empreintes, statut de la signature |

## Les notions

**Empreinte SHA-256**
:   « Signature numérique » d'un fichier : 64 caractères calculés à partir de son contenu. Le moindre pixel modifié donne une empreinte totalement différente. Comparer l'empreinte d'aujourd'hui à celle du scellement prouve que le fichier n'a pas changé.

**C2PA**
:   Standard international (Adobe, Microsoft, BBC…) qui attache à une image un **manifeste signé** : qui l'a produite, avec quel outil, à partir de quelles sources, et le type de contenu (`digitalSourceType` : ici une donnée satellite traitée par algorithme). Vérifiable par n'importe qui avec l'outil libre `c2patool`.

**Signature X.509 / PKI**
:   La plateforme possède sa propre **autorité de certification** (certificat racine `rootCA_cert.pem`) qui signe un certificat de scellement. L'**empreinte PKI** affichée identifie cette racine : un tiers peut vérifier que la preuve vient bien de cette instance. Les clés privées sont chiffrées sur le disque.

## Liste des dossiers

**Filtres** en haut : *Tous (n)*, puis un bouton par état d'intégrité avec son nombre — **Preuve intègre**, **Manifeste signé**, **Non vérifiable**, **Preuve altérée**. Un dossier *Preuve altérée* doit être signalé immédiatement.

Chaque carte de dossier montre :

| Élément | Contenu |
| --- | --- |
| Titre | nom du dossier `alerte_AAAAMMJJ_HHMMSS_xxxxxxxx` (date et heure UTC du scellement) |
| Badge | état d'intégrité calculé à l'affichage |
| Planche | aperçu cliquable de `radar_crop.jpg` (*Pas de planche* si absente) |
| Scellé … · n cible(s) · signature … | date, nombre de cibles, statut de signature du manifeste |
| Scène | identifiant Sentinel-1 d'origine |
| PKI | empreinte du certificat racine |
| Incidents | sévérité, référence et statut de chaque incident lié (clic = ouvrir) ; *Aucun incident lié* sinon |
| Fichiers | noms et tailles |

Ouvert depuis un incident (*Dossier de preuves*), le dossier concerné est **encadré** en cyan.

!!! info "Ce qui compte comme consultation"
    L'aperçu de la planche dans la liste et dans la fiche cible, et les vignettes, **ne sont pas** inscrits dans la chaîne de possession (ils s'affichent en permanence). **Cliquer** sur l'image pour l'ouvrir, **Vérifier** et **Exporter** sont tracés.

### États d'intégrité

| État | Condition |
| --- | --- |
| **Preuve intègre** (C2PA vérifié) | manifeste C2PA embarqué signé par un certificat émis par une **racine KRAKEN** (actuelle ou archivée), image et vignettes identiques aux empreintes de ce manifeste signé |
| **Manifeste signé** | pas de C2PA embarqué (bibliothèque indisponible au scellement), mais `manifest.json` porte une **signature ES256** valide du certificat de scellement KRAKEN et tous les fichiers correspondent |
| **Non vérifiable** | ancienne preuve dont `manifest.json` n'est pas signé : les empreintes concordent mais rien ne prouve qui les a écrites |
| **Preuve altérée** | un fichier manque ou a changé, la signature ne remonte pas à une racine KRAKEN (re-signature par une autre clé), ou le manifeste a été modifié |

!!! warning "Ce que la vérification garantit"
    Une signature C2PA « valide » ne suffit pas : n'importe qui peut signer une image avec sa propre clé. KRAKEN n'accepte que les signatures qui remontent à **sa** racine (`keys/rootCA_cert.pem`, et les anciennes racines conservées dans `keys/roots/`). Pour un tiers, l'ancre de confiance est l'empreinte SHA-256 de cette racine, à publier par l'exploitant.
    L'heure de signature est celle du serveur, sauf si une autorité d'horodatage RFC 3161 est configurée (`C2PA_TSA_URL`).

!!! info "Durée de vie des certificats"
    La racine vaut 10 ans, le certificat de scellement 1 an ; ce dernier est renouvelé automatiquement 30 jours avant expiration par la même racine. Une racine remplacée est archivée : les preuves qu'elle a signées restent vérifiables. La clé privée de la racine reste sur l'instance (chiffrée par la clé maître) pour permettre ce renouvellement.

## Vérifier (analyste et plus)

Recalcule **maintenant** l'empreinte de chaque fichier et la compare au manifeste scellé :

| Colonne | Signification |
| --- | --- |
| **Fichier** | Nom du fichier |
| **SHA-256 scellé** | Empreinte enregistrée au scellement |
| **Contrôle** | ✅ identique / ❌ modifié ou manquant |

Le rapport indique aussi l'état du **manifeste C2PA embarqué** vis-à-vis des racines KRAKEN (*Trusted* attendu ; *Valid* = signé par une autre clé, refusé) et l'empreinte de la racine PKI.

## Exporter (ZIP)

Paquet destiné à la procédure judiciaire :

| Fichier ajouté | Contenu |
| --- | --- |
| tous les fichiers scellés | tels quels |
| `verification.json` | contrôle d'intégrité réalisé au moment de l'export |
| `chain_of_custody.json` | **chaîne de possession** : chaque consultation, vérification et export de ce dossier (date, utilisateur, adresse IP), extraite du journal d'audit chaîné |
| `incidents.json` | incidents liés au dossier |
| `rootCA_cert.pem` | certificat racine (ancre de confiance) pour vérifier la signature, par exemple avec `c2patool` |
| `LISEZMOI.txt` | explication du contenu |

L'export est lui-même tracé dans l'audit et dans la chronologie des incidents liés.

!!! warning "Accès"
    Les preuves ne sont **jamais** accessibles par un lien public : chaque fichier passe par une session authentifiée, et chaque consultation est inscrite dans la chaîne de possession.

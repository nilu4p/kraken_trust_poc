# Liste de surveillance

<p class="where">Administration → <strong>Liste de surveillance</strong>.</p>

Navires à signaler dès qu'ils apparaissent : la **liste INN combinée** des ORGP et des entrées manuelles (renseignement, signalements).

## Import de la liste INN combinée (administrateur)

1. Télécharger le fichier sur [iuu-vessels.org](https://www.iuu-vessels.org/Home/Download) (liste combinée tenue par TMT).
2. **Choisir le fichier** : XLS, XLSX ou CSV, 10 Mo maximum. Toutes les feuilles sont lues ; la bonne est reconnue automatiquement.
3. L'import **remplace** la version précédente de la liste INN (les entrées manuelles sont conservées).

Compte rendu affiché : nombre de navires actuellement listés, format, feuille, présentation reconnue, **navires radiés ignorés** (colonne « CurrentlyListed » à faux), et combien ont un IMO, un MMSI ou un indicatif radio. Les indicatifs de remplissage du type `4SFXXXX` sont ignorés.

## Ajout manuel (analyste et plus)

Nom, MMSI (9 chiffres), IMO (7 chiffres), pavillon, motif. Une entrée manuelle se **Retire** (administrateur).

## Tableau des navires surveillés

Panneau **Navires surveillés · n**, avec une recherche (**Rechercher** : nom, MMSI, IMO, indicatif). Colonnes : **Navire** (et alias), **MMSI**, **IMO**, **Indicatif**, **Pavillon**, **Engin / type**, **Motif** (motif d'inscription et ORGP), **Source** (liste INN combinée, nationale, autre, manuelle), et **Retirer** (administrateur).

## Comment un navire est reconnu

Les identifiants sont essayés **du plus fort au plus faible** ; la correspondance retenue est indiquée (« Correspondance : IMO ») :

1. **MMSI** (9 chiffres exacts)
2. **IMO** (7 chiffres exacts)
3. **Indicatif radio** (IRCS)
4. **Nom ou alias ORGP** normalisé (majuscules, sans accents ni ponctuation, au moins 4 caractères)

!!! warning "Correspondance par le nom"
    Un nom peut être porté par plusieurs navires, et un navire INN change souvent de nom et de pavillon. Une correspondance sur le **nom seul** doit être confirmée (IMO, photo, historique) avant toute action.

## Effets

- flèche AIS **rouge cerclée** sur la carte et mention ⚠ dans les bulles (AIS, événements GFW) ;
- incident **AIS : liste INN** (P1) dès que le navire émet dans la ZEE, notifié sur **#surveillance** ;
- +30 points au score de risque d'une cible associée.

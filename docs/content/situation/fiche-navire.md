# Fiche navire AIS (fusion)

<p class="where">Clic sur une flèche AIS de la carte.</p>

## Bulle d'information

| Ligne | Contenu |
| --- | --- |
| Titre coloré | **Catégorie** du navire (Pavillon français, Pêcheur étranger, Liste INN…) |
| Nom · MMSI | Nom déclaré dans l'AIS et identifiant MMSI |
| Pavillon · type | Pays déduit du MMSI ; « pêche (engin) » si navire de pêche, sinon le **type AIS** (30 = pêche, 52 = remorqueur, 60-69 = passagers, 70-79 = cargo, 80-89 = pétrolier…) |
| Vitesse · vu | Vitesse sur le fond en nœuds (1 nd = 1,852 km/h), heure UTC du dernier signal, flux si autre qu'aisstream (AISHub, VesselAPI, VMS) |
| ⚠ Liste … | Si le navire est sur la liste de surveillance : source et motif d'inscription |

## Fiche fusion

Sous la bulle, la plateforme **croise toutes les sources** sur ce navire (requête à chaque ouverture) :

| Ligne | Signification |
| --- | --- |
| **Sources (24 h)** | Nombre de positions reçues par chaque flux dans les 24 dernières heures (`aisstream.io 120 · AISHub 35`). Un navire entendu par un seul flux est en limite de couverture |
| **sous licence (VMS)** | Le navire a été vu dans un import VMS : il est autorisé à pêcher et n'est pas signalé par la règle « pêche étrangère » |
| **IMO · indicatif** | Identifiants déclarés dans les messages statiques AIS (complétés par le registre VesselAPI s'ils manquent) |
| **Registre (VesselAPI)** | Type et sous-type, pavillon d'immatriculation, année de construction, longueur, jauge brute, port d'attache et **anciens noms** (en orange : un navire renommé plusieurs fois mérite une vérification). Interrogé à l'ouverture de la fiche si le navire n'est pas encore décrit (1 appel du quota mensuel, conservé 30 jours) |
| **GFW Insights (12 mois)** | Indicateurs de risque calculés par Global Fishing Watch : inscription INN d'une ORGP, nombre de pêches dans une AMP intégrale, pêches sans autorisation ORGP connue, coupures AIS, changements de pavillon, et **couverture AIS** (% du temps où le navire a été entendu) |
| **⚠ Sanctions** | Correspondance OpenSanctions : nom de l'entité, **score** de similarité (0 – 1), listes concernées (`us_ofac_sdn`…), lien vers la fiche |
| *pas encore criblé* | Le navire n'a pas encore été enrichi (au plus 10 navires par minute) ou aucune clé n'est configurée |
| **Incidents** | Références des incidents liés à ce navire (lien vers le dossier) |
| **Dernière position … via …** | Heure UTC et flux de la position la plus récente |

!!! tip "Lire la couverture AIS"
    Une couverture faible (ex. 30 %) chez un navire de pêche signifie qu'il est souvent inaudible : coupures volontaires ou zones sans récepteur. C'est un indicateur de risque, pas une preuve.

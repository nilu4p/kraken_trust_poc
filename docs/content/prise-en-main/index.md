# Interface générale

<p class="where">Visible sur toutes les pages, une fois connecté.</p>

## Connexion

- Chaque utilisateur a un **compte nominatif** (pas de compte partagé) : toutes ses actions sont tracées à son nom dans l'[audit](../audit.md).
- **Première connexion** : le mot de passe donné par l'administrateur est *temporaire* ; la plateforme impose d'en choisir un nouveau avant d'accéder à quoi que ce soit.
- **Règles du mot de passe** : au moins 12 caractères, au moins 3 familles parmi minuscules / majuscules / chiffres / symboles, et il ne doit pas contenir l'identifiant.
- **Verrouillage** : après 5 échecs, le compte est bloqué 15 minutes (un administrateur peut le débloquer en réinitialisant le mot de passe). Le message d'erreur est volontairement le même que l'identifiant existe ou non.
- **Durée de session** : 8 heures au maximum, et déconnexion automatique après **30 minutes d'inactivité**. Vous revenez alors à l'écran de connexion.

## Le bandeau du haut

De gauche à droite :

| Élément | Signification |
| --- | --- |
| **KRAKEN-Trust · SOC maritime · ZEE Guyane** | Nom de la plateforme et périmètre surveillé |
| **Onglets de navigation** | Situation, Incidents, Commandement, Graphe, Preuves, Règles, Audit, Administration, Documentation : une icône chacun (nom au survol, écrit en toutes lettres pour l'écran ouvert). Sur petit écran, le bouton **☰** ouvre un tiroir avec les écrans et les compteurs. Vous ne voyez que les onglets autorisés par votre [rôle](roles.md) |
| **Horloges** | Trois heures en continu (voir ci-dessous). Masquées sur petit écran |
| **Compteurs (KPI)** | Indicateurs de la file d'incidents (voir ci-dessous). Affichés sur les écrans larges (≥ 1 024 px) ; dans le tiroir ☰ sinon |
| **Menu utilisateur** | Votre nom et votre rôle ; clic → *Changer mon mot de passe* ou *Se déconnecter* |

Sur grand écran, chaque écran est une icône du bandeau (son nom s'affiche au survol ; celui de l'écran ouvert est écrit en toutes lettres). Sur téléphone ou tablette étroite, le bouton **☰** ouvre un tiroir qui liste les écrans avec leur description. La touche **Échap** ferme menus et fenêtres ; **Tab** fait apparaître le lien « Aller au contenu ».

### Les trois horloges

| Horloge | Fuseau | Pourquoi |
| --- | --- | --- |
| **UTC · Zulu** | UTC+0 | Référence de toutes les opérations maritimes et de **toutes les heures affichées dans la plateforme** (suffixe « Z ») : passages satellite, positions AIS, journaux |
| **Métropole** | Europe/Paris (UTC+1 en hiver, UTC+2 en été, calculé automatiquement) | Chaîne de commandement nationale |
| **Cayenne** | America/Cayenne (UTC-3, pas d'heure d'été) | Heure locale des moyens d'intervention en Guyane |

!!! example "Conversion"
    Un passage Sentinel-1 à **21:34 Z** a eu lieu à **18:34** à Cayenne et à **23:34** à Paris en heure d'été.

### Les compteurs du bandeau

Rafraîchis toutes les 30 secondes et après chaque action sur un incident.

| Compteur | Définition | Clic |
| --- | --- | --- |
| **P1 · n** (rouge) | Nombre d'incidents **ouverts** de sévérité P1 (critique) | ouvre la file d'incidents |
| **P2 · n** (orange) | Nombre d'incidents ouverts de sévérité P2 (élevée) | ouvre la file d'incidents |
| **SLA · n** | Incidents ouverts dont une échéance (triage ou résolution) est **dépassée**. Rouge dès qu'il y en a un | — |
| **Ouverts · n · non assignés m** | Tous les incidents non clos, dont ceux qu'aucun opérateur n'a pris | — |

Voir [Sévérités, SLA et indicateurs](../incidents/severite-sla.md).

## Les pages

| Page | Rôle minimum | Pour quoi faire |
| --- | --- | --- |
| [Situation](../situation/index.md) | Observateur | Carte opérationnelle : cibles radar, navires AIS, couches de contexte, lancement des scans |
| [Incidents](../incidents/index.md) | Observateur | File de traitement des anomalies, dossier, playbook, escalade |
| [Preuves](../preuves.md) | Observateur | Dossiers scellés, contrôle d'intégrité, export judiciaire |
| [Audit](../audit.md) | Superviseur | Journal infalsifiable de toutes les actions |
| [Administration](../administration/index.md) | Administrateur | Comptes, clés, paramètres, listes, sources |
| **Documentation** | tous | Cette base de connaissances (nouvel onglet) |

L'adresse de la page suit le format `#/incidents/12` : vous pouvez copier un lien vers un incident précis et l'envoyer à un collègue (il devra être connecté).

## Changer son mot de passe

Menu utilisateur → **Changer mon mot de passe** : saisir le mot de passe actuel, puis deux fois le nouveau (« Les deux saisies diffèrent » s'ils ne correspondent pas). Les règles de complexité s'appliquent et le nouveau mot de passe doit être différent de l'ancien. **Vos autres sessions sont fermées** (autres postes, autres navigateurs) : utile si vous pensez qu'un poste est resté connecté.

Mot de passe oublié : demandez à un administrateur de le **réinitialiser** (vous recevrez un mot de passe temporaire).

## Raccourcis et astuces

| Action | Raccourci |
| --- | --- |
| Fermer la fiche cible | ++escape++ |
| Cible suivante / précédente (fiche ouverte) | ++arrow-right++ / ++arrow-left++ |
| Rechercher dans la documentation | ++s++ ou ++slash++ |
| Ouvrir un incident précis | adresse `…/#/incidents/<numéro>` |
| Ouvrir un dossier de preuves précis | adresse `…/#/evidence/<dossier>` |

Les raccourcis de la fiche cible sont inactifs quand le curseur est dans un champ de saisie.

## Taille d'écran

L'application est conçue pour un poste de travail (≥ 1 280 px). Les libellés des onglets ne s'affichent que sur les très grands écrans (≥ 1 760 px) ; en dessous, les onglets sont des icônes (le nom apparaît au survol). Les compteurs disparaissent sous 1 024 px et les horloges sous 1 280 px ; le menu utilisateur reste toujours visible. Par ailleurs, le panneau latéral passe par-dessus la carte et, sous 1 280 px, la liste des incidents se masque quand un dossier est ouvert.

## Si une page affiche une erreur

| Message | Signification | Que faire |
| --- | --- | --- |
| **Nouvelle version disponible** | la plateforme a été mise à jour pendant que la page était ouverte | **Recharger la page** |
| **Cette vue a rencontré une erreur** | un problème d'affichage limité à cette page ; les autres restent utilisables | **Réessayer** ; si cela persiste, signaler l'heure UTC et le message affiché |

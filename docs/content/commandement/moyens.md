# Moyens d'intervention

<p class="where">Onglet <strong>Commandement</strong> → <strong>Moyens</strong>. Lecture pour tous ; ajout, modification, suppression et disponibilité réservés aux superviseurs (tracé dans l'audit : <code>asset.create</code>, <code>asset.update</code>, <code>asset.delete</code>).</p>

Les moyens alimentent l'[aide à la décision](decision.md) : sans eux, aucune interception ne peut être proposée.

## Champs

| Champ | Rôle dans le calcul | Conseil |
| --- | --- | --- |
| **Nom** | affiché dans les options et les rapports | nom d'usage du bâtiment ou de l'aéronef |
| **Type** | patrouilleur, vedette, hélicoptère, avion de surveillance | — |
| **Point de départ en mer** (nom, latitude, longitude) | origine de la route en ligne droite | la **sortie du port** (embouchure, chenal), pas le quai : la route ne contourne pas la terre |
| **Vitesse de transit** (nœuds) | vitesse de ralliement | vitesse soutenable par l'état de mer habituel, pas la vitesse maximale |
| **Distance franchissable** (NM) | aller + retour doit tenir dedans | autonomie réelle avec réserve de sécurité |
| **Délai d'appareillage** (min) | ajouté avant le transit | inclut le rappel d'équipage et la descente du fleuve |
| **Peut arraisonner** | seul un moyen arraisonneur ouvre l'option *Interception et contrôle* ; les autres servent à la *levée de doute* | avion : non ; vedette avec équipe de visite : oui |
| **Disponibilité** | seuls les moyens *Disponibles* sont réalisables | à mettre à jour à chaque prise de quart et à chaque engagement |

## Moyens installés par défaut

Une flotte **générique** est installée au premier lancement pour que l'aide à la décision fonctionne d'emblée : patrouilleur hauturier et vedette côtière (sortie du Mahury, Dégrad des Cannes), vedette du Maroni (embouchure, Saint-Laurent-du-Maroni), avion de surveillance maritime et hélicoptère (Cayenne – Félix Éboué). **Leurs valeurs sont indicatives** : la colonne *Mis à jour* affiche « valeurs génériques » et l'aide à la décision le rappelle tant qu'un superviseur ne les a pas modifiées. Supprimer tous les moyens ne les réinstalle pas.

!!! tip "Prise de quart"
    Passez la disponibilité de chaque moyen en revue au début du quart (sélecteur directement dans le tableau). Un moyen engagé sur une autre mission doit être *Engagé* : il disparaît des options réalisables.

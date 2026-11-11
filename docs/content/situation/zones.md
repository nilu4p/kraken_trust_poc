# Zones de surveillance dessinées

<p class="where">Situation → panneau latéral → <strong>Couches</strong> → Zones de surveillance. Dessin et suppression : superviseur et plus.</p>

Une zone de surveillance est un **polygone nommé** dessiné sur la carte : estuaire, chenal, zone de frayère, abord d'une aire protégée, secteur de patrouille… Elle s'affiche en pointillés de sa couleur et sert de **critère dans les règles sur mesure** (« se trouve dans la zone « Estuaire du Maroni » »).

## Dessiner

1. **Dessiner une zone sur la carte** (section *Zones de surveillance* de l'onglet Couches).
2. **Cliquer** sur la carte pour chaque sommet (le premier est marqué d'un cercle plus grand). *Annuler le dernier point* corrige une erreur.
3. **Terminer** (3 sommets au moins), puis donner un **nom**, une description facultative et une couleur. **Enregistrer la zone**.
4. *Abandonner* efface le tracé en cours.

La zone est refusée si ses côtés **se croisent** (redessiner sans croisement), si elle a plus de 200 sommets, ou si elle est minuscule. Le dessin est désactivé en rejeu.

## Utiliser dans une règle

Onglet **Règles** → *Créer une règle* → critère **Zone de surveillance (dessinée)** → *dans* ou *hors de* → choisir une ou plusieurs zones. Le bouton **Tester** montre les navires détectés et, pour chacun, la zone où il se trouve.

## Supprimer

Bouton *supprimer* de la liste. Une zone **utilisée par une règle active** ne peut pas être supprimée (la règle cesserait de détecter sans prévenir) : la liste indique les règles concernées ; désactivez ou modifiez la règle d'abord. Création, modification et suppression sont tracées dans l'audit (`zones.create`, `zones.update`, `zones.delete`).

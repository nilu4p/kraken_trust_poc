# Administration — Utilisateurs

<p class="where">Onglet <strong>Administration</strong> (administrateurs). Sous-onglets : Utilisateurs, Clés & intégrations, Paramètres, Liste de surveillance, Navires autorisés, Catalogue des sources, [Sources personnalisées](sources-personnalisees.md), Santé des sources.</p>

## Créer un compte

Panneau **Nouveau compte** : **Identifiant** (connexion), **Nom affiché**, **Rôle**. La plateforme génère un **mot de passe temporaire**, affiché **une seule fois** : transmettez-le par un canal sûr puis cliquez *J'ai transmis le mot de passe*. L'utilisateur devra le changer à sa première connexion.

En ligne de commande sur le serveur : `make user NAME=alice ROLE=analyst`, ou `make admin` pour un administrateur (voir [Exploitation](../exploitation.md)).

## Liste des comptes

| Colonne | Contenu |
| --- | --- |
| **Compte** | identifiant et nom affiché |
| **Rôle** | modifiable directement (ferme les sessions de l'utilisateur) |
| **État** | **Actif**, **Désactivé**, **Verrouillé** (trop d'échecs de connexion, 15 min), **MDP temporaire** (doit changer son mot de passe) |
| **Dernière connexion** | date et heure |
| **Actions** | **Réinitialiser** (après confirmation) : nouveau mot de passe temporaire, sessions fermées, verrouillage levé · **Désactiver** (après confirmation) / **Réactiver** : bloque l'accès sans supprimer le compte ni son historique |

Les comptes ne sont jamais supprimés : leurs actions doivent rester attribuables dans l'audit. Un administrateur ne peut ni se désactiver lui-même ni retirer son propre rôle administrateur (il faut un second administrateur pour cela).

## Matrice des droits

Tableau des capacités par rôle (« autorisé » / « non autorisé ») : voir [Rôles et droits](../prise-en-main/roles.md).

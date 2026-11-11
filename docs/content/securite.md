# Sécurité

| Domaine | Mesure | Ce que cela signifie pour vous |
| --- | --- | --- |
| Identité | comptes nominatifs, rôles cumulatifs | toute action vous est attribuée |
| Mots de passe | hachage **scrypt** salé ; 12 caractères et 3 familles minimum ; mot de passe temporaire à changer | même un administrateur ne peut pas lire votre mot de passe |
| Connexion | message d'erreur identique que l'identifiant existe ou non ; verrouillage 15 min après 5 échecs (compteur atomique : des tentatives simultanées comptent toutes) ; limitation par compte et par adresse IP, lue derrière le proxy sans faire confiance à l'en-tête `X-Forwarded-For` fourni par le client ; seconde limitation dès le proxy nginx (10 tentatives par minute et par adresse) ; essais du mot de passe actuel limités aussi au changement de mot de passe | les tentatives de force brute échouent |
| Session | jeton aléatoire dans un cookie **HttpOnly, SameSite=Strict**, préfixé **`__Host-`** en HTTPS (Secure, lié à l'hôte : un sous-domaine voisin ne peut ni le poser ni l'écraser) ; seule son empreinte est stockée ; 8 h maximum, 30 min d'inactivité ; fermée au changement de mot de passe ou de rôle | quitter son poste sans se déconnecter reste limité dans le temps |
| CSRF | jeton exigé sur toute action | un site malveillant ne peut pas agir en votre nom |
| Autorisations | vérifiées **par le serveur** sur chaque requête | masquer un bouton n'est pas la protection : l'action elle-même est refusée |
| Secrets | clés d'API, webhook et sources personnalisées **chiffrés** (Fernet) sous une clé maître ; clés privées de signature chiffrées | une copie du disque ne livre pas les clés |
| Preuves | accès authentifié uniquement, chemins contrôlés, chaque accès tracé ; manifestes C2PA signés par la PKI de l'instance | chaîne de possession |
| Audit | journal en ajout seul, **chaîné SHA-256**, vérifié toutes les heures (par lots : mémoire bornée quelle que soit sa taille) | une falsification se voit |
| Documentation | la base de connaissances (pages, fichiers, index de recherche) n'est servie qu'aux sessions valides : le proxy vérifie la session sur chaque requête, sinon renvoie vers la connexion ; le serveur de notifications (`/notifs/`, canal #securite) est réservé aux rôles superviseur / responsable et administrateur | la doc interne (procédures, seuils, architecture) ne fuit pas hors des comptes autorisés |
| Sorties | webhook en HTTPS vers des hôtes publics seulement (sauf liste autorisée) ; [sources personnalisées](administration/sources-personnalisees.md#securite) vers des adresses publiques seulement (chaque redirection revérifiée, adresse réellement connectée contrôlée contre le *DNS rebinding*), secret jamais envoyé en clair ni à un autre hôte (redirections et pages suivantes comprises) ; tuiles Sentinel-2 rendues sur l'emprise du territoire seulement | la plateforme ne peut pas servir de rebond vers le réseau interne |
| Navigateur | politique de sécurité du contenu stricte (scripts et polices de la plateforme seulement : polices IBM Plex hébergées sur l'instance, aucune requête vers un CDN), interdiction d'intégration dans une autre page, capteurs et périphériques du poste désactivés (`Permissions-Policy`), HSTS, pas de référent envoyé (sauf l'origine aux serveurs de tuiles) | limite le vol de données par script ou par cadre ; l'adresse des agents n'est communiquée à aucun tiers |
| Conteneurs | utilisateurs non-root, système de fichiers en lecture seule, privilèges retirés, API joignable uniquement par la console | surface d'attaque réduite |

!!! danger "La clé maître"
    `secrets/master_key` (secret Docker, ou la variable `MASTER_KEY`) déchiffre toutes les clés d'API et les clés de signature. Sans elle, ces secrets et les sauvegardes sont **définitivement illisibles**. Elle n'est **pas** incluse dans les sauvegardes `make backup` : conservez-en une copie à part, **hors de la machine**.

## Bonnes pratiques

- Ne partagez jamais un compte ; demandez-en un à l'administrateur.
- Verrouillez votre session (ou déconnectez-vous) en quittant le poste.
- Une notification **#securite** inattendue (verrouillage, clés modifiées, nouvel administrateur) doit être signalée.
- Pour une preuve judiciaire, utilisez toujours l'**export ZIP** : il contient la chaîne de possession.

## Hygiène des dépendances

| Mesure | Détail |
| --- | --- |
| Versions exactes | Python (`requirements.txt`) et interface (`package-lock.json` avec empreintes d'intégrité) : chaque construction installe exactement les mêmes paquets |
| Installation sans scripts | `npm ci --ignore-scripts` : aucun code de dépendance ne s'exécute à l'installation |
| Audit de vulnérabilités | `pip-audit` (Python) et `npm audit` (interface) ; dernière vérification (octobre 2026) : aucune vulnérabilité connue dans les dépendances Python directes ni dans les paquets livrés au navigateur |
| Fichiers importés | Les feuilles XLSX passent par `defusedxml` (pas d'expansion d'entités ni d'entités externes) |
| Risque résiduel connu | `braces` (outil de construction de Tailwind) : déni de service sur motifs de fichiers malveillants, sans correctif publié ; non exploitable ici (motifs fixes, outil absent de l'image servie) |

Pour refaire l'audit : `pip-audit -r backend/requirements.txt` et `npm audit` dans `frontend/`.

## Verrouillage des comptes

Après 5 échecs, un compte est verrouillé 15 minutes : cela bloque la force brute mais permet à quelqu'un qui connaît un identifiant de le verrouiller volontairement. En cas d'abus, un administrateur peut **réinitialiser** le mot de passe (ce qui lève le verrou), et la limitation par adresse IP (10 tentatives par minute) freine l'attaquant.

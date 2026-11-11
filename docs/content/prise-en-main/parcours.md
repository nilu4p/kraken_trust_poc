# Parcours type d'un opérateur

Ce parcours enchaîne tout le cycle, du passage satellite à la transmission au parquet. Chaque étape est détaillée dans les [guides pratiques](../guides/index.md).

## 1. Prise de quart

1. Connectez-vous et regardez le **bandeau** : nombre de P1/P2 ouverts, SLA dépassés.
2. Dans *Situation*, ouvrez l'onglet **Contexte** du panneau latéral → **Couverture temporelle** : vérifiez que l'AIS est vivant (dernier signal de quelques minutes) et la date du dernier scan radar.
3. Ouvrez *Incidents* → filtre **Non assignés**.

## 2. Nouvelle détection

Un incident peut naître de deux façons :

- un **scan Sentinel-1** (manuel ou veille automatique) trouve un écho radar sans AIS → incident *Radar : navire sans AIS* ;
- une **règle 24/7** sur l'AIS se déclenche entre deux passages (pêche étrangère, liste INN, coupure AIS, rendez-vous en mer, sanctions, aire protégée).

Les P1/P2 sont aussi poussés sur le canal **#alertes** ([notifications](../notifications.md)).

## 3. Triage (analyste)

1. Cliquez la cible sur la carte (ou l'incident dans la file) : la **fiche cible** s'ouvre.
2. Lisez la **fiabilité** (est-ce un navire ?) et ses justifications, puis le **score de risque** et ses raisons.
3. Regardez la **vignette radar** et les **aperçus optiques** autour de la cible.
4. Décidez :
    - **Prendre en charge** → statut *En triage* (arrête le chronomètre de triage) ;
    - ou **Fausse alarme** → l'incident est clos *Fausse alarme* et l'endroit est mémorisé : la prochaine détection au même point verra sa fiabilité baisser de 0,45.

## 4. Qualification (analyste)

Suivez le **playbook** dans le dossier d'incident (vérifier l'AIS, l'imagerie optique, les lumières nocturnes, identifier le navire), commentez, puis passez à **Qualifié**.

## 5. Escalade et intervention (superviseur)

1. **Escalader** : choisir l'autorité (CROSS AG, Préfet AEM, Marine nationale, DM Guyane, Gendarmerie maritime, Douanes, Parquet) et saisir sa référence. Notification sur **#escalades**.
2. **Intervention** quand un moyen nautique ou aérien est engagé.

## 6. Clôture et preuves

1. Clore avec une **résolution** (Pêche INN confirmée, Activité légitime, Fausse alarme, Doublon, Sans suite).
2. Pour une procédure : *Preuves* → **Vérifier** puis **Exporter** le dossier ZIP (fichiers scellés, rapport d'intégrité, chaîne de possession). Chaque consultation est tracée.

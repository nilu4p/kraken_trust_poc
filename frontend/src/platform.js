// Identity of this platform for the generic console core (shell, login, audit, accounts…):
// names, labels and settings read by components/, lib/, services/client.js and the views.

export const PLATFORM = {
  id: "kraken", // namespace of browser events and local storage keys
  name: "KRAKEN-Trust",
  tagline: "SOC maritime · ZEE Guyane",
  loginTitle: "Centre de surveillance ZEE Guyane",
  loginNotice: "Accès réservé aux personnels habilités. Toutes les actions sont journalisées.",
  loadingLabel: "Connexion au centre de surveillance…",
  // Display time zone of every date ("UTC" = ISO "AAAA-MM-JJ HH:MM Z": maritime operations run on Zulu time).
  timeZone: "UTC",
  apiTimeoutMs: 30000,
  // Geographic scope, as written in sentences ("emprise de …").
  territoryLabel: "la ZEE de Guyane",
  // Prefixes of the actions recorded by the backend (audit.record calls).
  auditFamilies: [
    { id: "auth.", label: "Connexions" },
    { id: "user.", label: "Comptes" },
    { id: "scan.", label: "Scans" },
    { id: "incident.", label: "Incidents" },
    { id: "evidence.", label: "Preuves" },
    { id: "report.", label: "Rapports signés" },
    { id: "rules.", label: "Règles" },
    { id: "watchlist.", label: "Surveillance INN" },
    { id: "authorized.", label: "Navires autorisés" },
    { id: "vms.", label: "VMS" },
    { id: "zones.", label: "Zones" },
    { id: "asset.", label: "Moyens" },
    { id: "sensor.", label: "Capteurs" },
    { id: "custom_source.", label: "Sources perso." },
    { id: "system.", label: "Système" },
  ],
  // Mirrors the backend RBAC (app/api/deps.py + app/soc/incidents.py): minimum role per capability.
  capabilities: [
    { group: "Situation", label: "Consulter la carte, les cibles, la météo, l'imagerie", min: "viewer" },
    { group: "Situation", label: "Lancer un scan Sentinel-1", min: "analyst" },
    { group: "Incidents", label: "Consulter la file et les dossiers d'incident", min: "viewer" },
    { group: "Incidents", label: "Prendre en charge, qualifier, commenter, cocher le playbook", min: "analyst" },
    { group: "Incidents", label: "S'attribuer un incident", min: "analyst" },
    { group: "Incidents", label: "Classer au triage : fausse alarme, activité légitime, doublon", min: "analyst" },
    { group: "Incidents", label: "Affecter un incident à un autre opérateur", min: "supervisor" },
    { group: "Incidents", label: "Escalader vers une autorité AEM (CROSS, Marine, DM, Parquet…)", min: "supervisor" },
    { group: "Incidents", label: "Engager l'intervention, clore avec n'importe quelle résolution, rouvrir", min: "supervisor" },
    { group: "Commandement", label: "Consulter l'aide à la décision, les moyens et le registre des rapports", min: "viewer" },
    { group: "Commandement", label: "Lancer une analyse optique / thermique (Sentinel-2, Landsat) autour d'une cible", min: "analyst" },
    { group: "Commandement", label: "Rédiger et émettre (signer) un compte rendu d'incident ; vérifier, exporter un rapport", min: "analyst" },
    { group: "Commandement", label: "Émettre un SITREP ; gérer les moyens d'intervention et leur disponibilité", min: "supervisor" },
    { group: "Preuves", label: "Consulter les dossiers de preuves scellés", min: "viewer" },
    { group: "Preuves", label: "Vérifier l'intégrité et exporter un dossier judiciaire (ZIP)", min: "analyst" },
    { group: "Surveillance", label: "Ajouter un navire à la liste de surveillance", min: "analyst" },
    { group: "Surveillance", label: "Importer des positions VMS (navires sous licence)", min: "supervisor" },
    { group: "Surveillance", label: "Ajouter / retirer un navire autorisé (licence)", min: "supervisor" },
    { group: "Surveillance", label: "Importer la liste des navires autorisés", min: "admin" },
    { group: "Surveillance", label: "Importer la liste INN, retirer des entrées", min: "admin" },
    { group: "Contrôle", label: "Consulter et vérifier le journal d'audit ; voir le serveur de notifications", min: "supervisor" },
    { group: "Administration", label: "Créer / désactiver des comptes, changer les rôles, réinitialiser un mot de passe", min: "admin" },
    { group: "Administration", label: "Gérer les clés API et le webhook (coffre chiffré)", min: "admin" },
    { group: "Administration", label: "Modifier les paramètres opérationnels", min: "admin" },
  ],
};

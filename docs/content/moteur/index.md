# Moteur et calculs

La plateforme combine deux chaînes complémentaires :

```text
                  ┌───────────────────── Passage satellite (quelques fois par semaine) ─────────────────────┐
 Sentinel-1 ──▶ Détection SAR (CFAR) ──▶ Fiabilité ──▶ Corrélation AIS/VMS ──▶ Risque ──▶ Scellement ──▶ Incident
                                                            ▲
 AIS (aisstream, AISHub) + VMS ──▶ Historique des positions ┤
                                                            └──▶ Règles 24/7 (chaque minute) ─────────────▶ Incident
 GFW, OpenSanctions, WDPA, Copernicus Marine, ERDDAP, Open-Meteo ──▶ Fusion / contexte (dans les deux chaînes)
```

| Page | Question traitée |
| --- | --- |
| [Détection radar](detection-sar.md) | Comment trouve-t-on un navire dans une image radar ? |
| [Indice de fiabilité](fiabilite.md) | Est-ce vraiment un navire ? |
| [Corrélation radar / AIS](correlation-ais.md) | Ce navire déclarait-il sa position ? |
| [Score de risque](risque.md) | Ce navire sans AIS est-il suspect ? |
| [Règles 24/7](regles.md) | Que surveille-t-on entre deux passages satellite ? |
| [Fusion multi-sources](fusion.md) | Comment les sources se complètent-elles ? |
| [Corroboration multi-capteurs](corroboration.md) | Un autre capteur (optique, lumières nocturnes, GFW) confirme-t-il la cible radar ? |
| [Détection optique et nocturne](detection-multicapteur.md) | Comment repère-t-on un navire sur une image Sentinel-2 / Landsat ou de nuit (VIIRS) ? |
| [Avis de l'IA (xView3)](ia.md) | Que pense un modèle d'IA de la cible radar (information seule) ? |

Tous les seuils marqués d'un nom technique (`cfar_k`) sont réglables dans [Paramètres](../administration/parametres.md).

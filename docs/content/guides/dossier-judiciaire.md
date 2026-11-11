# Constituer un dossier judiciaire

1. **Incident** : vérifier que la chronologie est complète (qualification, escalade avec référence du parquet ou de l'autorité, intervention, résolution *Pêche INN confirmée*).
2. **Preuves** : depuis l'incident, *Observations → Dossier de preuves* (ou onglet *Preuves*).
3. **Vérifier** : le rapport doit montrer **chaque fichier identique** et le badge **Preuve intègre**. Si un fichier est *altéré*, ne l'utilisez pas et signalez-le.
4. **Exporter (ZIP)** : le paquet contient les images scellées, le manifeste, le rapport d'intégrité, la **chaîne de possession**, les incidents liés, le certificat racine et un fichier LISEZMOI.
5. **Transmettre** le ZIP par un canal sûr ; noter dans la chronologie la référence de transmission.
6. Pour une vérification indépendante : l'outil libre `c2patool` lit le manifeste de `radar_crop.jpg` ; l'empreinte du certificat racine figure dans `manifest.json`.

!!! warning "Ne pas retoucher"
    Ne modifiez, recadrez ou recompressez jamais les images du dossier : l'empreinte changerait et la preuve perdrait sa valeur. Pour une illustration, faites une copie à part.

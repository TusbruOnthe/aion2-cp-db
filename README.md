# aion2-cp-db

Bases de classe partagées pour **AION 2 – Conseiller CP** (version Global).

Chaque jour, une tâche GitHub Actions lit sur le site officiel AION 2 les joueurs niveau 45 de chaque classe
(fiche, équipement, détail des objets des meilleurs) et publie `db/<région>_<classe>.json`.
L'appli télécharge ces fichiers au lieu de refaire la collecte sur chaque PC.

Données publiques du site officiel (aion2.plaync.com) uniquement.

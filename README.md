# Resultats FDJ

Page statique pour afficher les derniers resultats LOTO et EuroMillions, avec un historique du dernier mois.

La page est generee automatiquement par GitHub Actions puis publiee avec GitHub Pages. Une fois en ligne, elle peut etre ajoutee en raccourci sur le telephone comme une mini application.

## Ce que ca genere

- `index.html`: la page lisible dans le navigateur
- `resultats.json`: les memes donnees en JSON
- `manifest.webmanifest`: les infos pour le raccourci mobile

Les donnees viennent des pages et archives officielles FDJ.

## Est-ce gratuit ?

Oui, avec un compte GitHub gratuit, si le depot est public.

Il ne faut pas mettre de numero de telephone, mot de passe ou secret dans le depot. Ce projet n'en a pas besoin.

## Publier sur GitHub Pages

1. Cree un nouveau depot public sur GitHub, par exemple `resultats-fdj`.

2. Dans ce dossier, lie le depot GitHub puis pousse le code:

```bash
git add .
git commit -m "Publier la page FDJ"
git branch -M main
git remote add origin https://github.com/TON-PSEUDO/resultats-fdj.git
git push -u origin main
```

Si `git remote add origin` dit que `origin` existe deja, utilise plutot:

```bash
git remote set-url origin https://github.com/TON-PSEUDO/resultats-fdj.git
git push -u origin main
```

3. Sur GitHub, va dans `Settings > Pages`.

4. Dans `Build and deployment`, choisis `GitHub Actions` comme source.

5. Va dans l'onglet `Actions`, ouvre `Publish results page`, puis clique sur `Run workflow`.

6. Quand l'action est terminee, l'URL apparait dans `Settings > Pages`. Elle ressemble a:

```text
https://TON-PSEUDO.github.io/resultats-fdj/
```

## Raccourci sur le telephone

Sur iPhone:

1. Ouvrir l'URL dans Safari.
2. Appuyer sur le bouton de partage.
3. Choisir `Ajouter a l'ecran d'accueil`.

Sur Android:

1. Ouvrir l'URL dans Chrome.
2. Ouvrir le menu.
3. Choisir `Ajouter a l'ecran d'accueil`.

## Mise a jour automatique

Le workflow `.github/workflows/publish-page.yml` se lance:

- les soirs de tirage
- le lendemain matin en rattrapage
- manuellement avec `Run workflow`

Ton Mac n'a pas besoin d'etre allume. GitHub regenere la page dans le cloud.

## Tester en local

```bash
python3 fetch_results.py
python3 -m http.server 8765 --directory dist
```

Puis ouvre:

```text
http://127.0.0.1:8765/
```

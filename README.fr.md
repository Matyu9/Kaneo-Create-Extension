# kaneo-templates

*[English version](README.md)*

Créez un projet [Kaneo](https://github.com/usekaneo/kaneo) et ses tâches prédéfinies à partir d'un modèle YAML, avec des échéances calculées par rapport à une date de fin.

![Le formulaire et la prévisualisation du tableau](docs/images/preview.png)

- Choisissez un workspace, un modèle, un nom et une date de fin ; la prévisualisation montre le tableau tel que Kaneo le créera.
- Échéances avant et après la date de fin : `-30d`, `-5b` (jours ouvrés), `+3d`, `J-7`…
- Colonnes personnalisées, priorités, labels (créés s'ils manquent) et sous-tâches.
- Deux modes de connexion : un compte de service, ou chaque utilisateur avec son propre compte Kaneo (device flow).
- Un petit conteneur, sans base de données, interface en français et en anglais, thèmes clair et sombre.

Testé avec Kaneo v2.46.2.

## Démarrage rapide

```sh
cp .env.example .env    # renseigner KANEO_BASE_URL et KANEO_TOKEN
mkdir templates && cp templates-examples/*.yml templates/
cp docker-compose.example.yml docker-compose.yml
docker compose up -d
```

Ouvrez http://127.0.0.1:8000.

**En mode `service` (par défaut), l'application n'a pas de connexion propre** : placez-la derrière un reverse proxy qui authentifie, ou renseignez `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD`. La configuration, le mode `user` et des exemples de reverse proxy sont dans [docs/deployment.md](docs/deployment.md) (en anglais).

### Mode `user`

Chaque utilisateur se connecte avec son compte Kaneo : l'application affiche un code, l'utilisateur l'approuve sur la page `/device` de Kaneo. Il peut aussi coller une clé API. Côté Kaneo, l'administrateur ajoute l'identifiant du client à `DEVICE_AUTH_CLIENT_IDS` **en gardant les valeurs par défaut** :

```
DEVICE_AUTH_CLIENT_IDS=kaneo-cli,kaneo-mcp,kaneo-templates
```

## Modèles

```yaml
version: 1
name: Réunion du conseil
end_date:
  label: Date de la réunion
columns: [À faire, En cours, Terminé]
tasks:
  - title: Envoyer la convocation
    due: -15d
    priority: high
    labels: [gouvernance]
  - title: Rédiger le procès-verbal
    due: +5b
    subtasks:
      - title: Faire signer
        due: +10b
```

Référence complète : [docs/templates.md](docs/templates.md) (en anglais). Exemples : [templates-examples/](templates-examples).

## Limites connues

- Les jours ouvrés vont du lundi au vendredi, sans jours fériés.
- Rien n'est annulé quand une création s'arrête en cours de route : le rapport indique ce qui a été créé et où l'opération s'est arrêtée.
- Les modèles s'éditent sous forme de fichiers ; pas d'éditeur dans l'interface.
- Les échéances sont fixées à midi UTC ; les tâches ne sont assignées à personne.

## Développement

Nécessite [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest                  # tests unitaires, sans réseau
uv run ruff check && uv run ruff format --check
scripts/build-css.sh           # après un changement de classes dans les gabarits (--watch pendant l'édition)
```

Lancer en local contre une instance Kaneo, avec un `.env` gardé hors du dépôt :

```sh
uv run --env-file ../.env kaneo-templates          # avec HOST=127.0.0.1 et TEMPLATES_DIR=templates-examples
uv run --env-file ../.env pytest -m integration    # crée puis supprime un projet jetable
```

La feuille de style est générée avec Tailwind CSS à partir de `src/kaneo_templates/web/tailwind.css` et versionnée : l'application n'a besoin d'aucun outil front pour tourner. La CI vérifie qu'elle est à jour.

Notes de conception : [SPEC.md](SPEC.md), [docs/decisions.md](docs/decisions.md), [docs/kaneo-api-notes.md](docs/kaneo-api-notes.md).

## Licence

MIT. La police Geist (`static/fonts`) est sous licence SIL Open Font License, htmx sous 0BSD, les icônes Lucide sous ISC.

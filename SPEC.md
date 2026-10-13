# kaneo-templates — Spécification v1

Module externe pour Kaneo : créer un projet avec ses tâches prédéfinies à partir d'un modèle, en planifiant les échéances par rapport à une date de fin de projet.

Nom de travail : `kaneo-templates`. Licence visée : MIT (comme Kaneo). Dépôt public GitHub.

---

## 1. Objectifs

- Choisir un modèle, un workspace, un nom de projet et (selon le modèle) une date de fin, puis créer le projet et toutes ses tâches dans Kaneo en un clic.
- Planifier des tâches avant et après l'échéance (J-7, J+3…) calculées à partir de la date de fin saisie.
- Rester indépendant de toute infrastructure : aucune valeur ni hypothèse propre à une installation dans le code.
- Se déployer en un conteneur, derrière le reverse proxy de l'utilisateur.

Hors périmètre v1 : éditeur de modèles dans l'UI, historique des créations, synchronisation avec les projets existants, annulation d'une création.

---

## 2. Stack

- Python 3.13, FastAPI, Jinja2, HTMX (servi en local, pas de CDN), httpx, pydantic v2, PyYAML.
- Pas de build front. CSS minimal écrit à la main, thème clair/sombre selon `prefers-color-scheme`.
- Conteneur `python:3.13-slim`, utilisateur non-root, un seul process uvicorn.
- Gestion des dépendances avec `uv` et `pyproject.toml`.
- Tests : pytest, respx (mock httpx), ruff pour le lint et le format.

---

## 3. Configuration (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `KANEO_BASE_URL` | requis | URL de l'API, ex. `https://kaneo.example.com/api` |
| `KANEO_PUBLIC_URL` | déduit de `KANEO_BASE_URL` | URL de l'interface Kaneo, pour les liens vers les projets créés |
| `AUTH_MODE` | `service` | `service` ou `user` (voir §4) |
| `KANEO_TOKEN` | — | Token API, requis si `AUTH_MODE=service` |
| `SECRET_KEY` | — | Clé de chiffrement des cookies, requise si `AUTH_MODE=user` |
| `DEVICE_CLIENT_ID` | `kaneo-templates` | `client_id` envoyé au device flow de Kaneo en mode `user` (voir §4) |
| `KANEO_WORKSPACE_ID` | — | Workspace présélectionné dans l'UI (facultatif, jamais imposé) |
| `TEMPLATES_DIR` | `/data/templates` | Dossier des modèles YAML |
| `APP_LANG` | `fr` | Langue de l'interface : `fr` ou `en` |
| `ROOT_PATH` | vide | Préfixe d'URL si l'appli est servie sous un sous-chemin |
| `BASIC_AUTH_USER` / `BASIC_AUTH_PASSWORD` | — | Protection HTTP Basic facultative, désactivée si vides |
| `LOG_LEVEL` | `info` | Niveau de logs |

Au démarrage, l'application valide la configuration et refuse de démarrer avec un message clair si une variable requise manque.

---

## 4. Modes d'authentification vers Kaneo

Le token ne doit jamais atteindre le navigateur en mode `service`.

**Mode `service`** : un token unique, fourni par `KANEO_TOKEN`. Tous les projets sont créés avec ce compte. L'application n'authentifie pas ses propres utilisateurs : elle doit être placée derrière un reverse proxy avec authentification (forward auth, SSO…) ou activer `BASIC_AUTH_*`. Le README l'indique en tête de la section déploiement.

Note : avec un Kaneo configuré en SSO seul, le compte de service est un utilisateur normal du fournisseur d'identité. Il se connecte une fois par device flow (par exemple avec l'appli en mode `user` ou un client CLI) pour obtenir le token à placer dans `KANEO_TOKEN`.

**Mode `user`** : la méthode principale est le device flow de Kaneo (RFC 8628).

1. L'utilisateur clique sur « Se connecter avec Kaneo ».
2. L'appli appelle `POST /api/auth/device/code` avec `client_id` = `DEVICE_CLIENT_ID`, puis affiche l'URL de vérification et le code utilisateur.
3. L'appli interroge `POST /api/auth/device/token` à l'intervalle indiqué jusqu'à approbation, refus ou expiration, et reçoit un token Bearer.

Méthode de secours : l'utilisateur colle manuellement un token Kaneo, vérifié par un appel à l'API.

Dans les deux cas, le token est conservé dans un cookie `HttpOnly`, `Secure`, `SameSite=Lax`, chiffré avec Fernet à partir de `SECRET_KEY`. Un bouton de déconnexion efface le cookie. Sur une réponse 401 de Kaneo, le cookie est effacé et l'utilisateur est renvoyé vers la connexion. Les projets sont créés au nom de l'utilisateur.

Prérequis côté Kaneo : l'administrateur ajoute la valeur de `DEVICE_CLIENT_ID` à la variable `DEVICE_AUTH_CLIENT_IDS` du serveur Kaneo, en conservant les valeurs par défaut (`kaneo-cli`, `kaneo-mcp`) dans la liste. Le README le documente.

Ajouts possibles plus tard : connexion OIDC directe, selon ce que Kaneo permet.

Protections communes : jeton CSRF sur les POST, en-têtes de sécurité de base (CSP, X-Content-Type-Options, Referrer-Policy), aucun token ni contenu de modèle dans les logs.

---

## 5. Format des modèles

Un fichier YAML par modèle dans `TEMPLATES_DIR`. L'identifiant est le nom de fichier sans extension. Les fichiers invalides sont ignorés avec un avertissement dans les logs et signalés dans l'UI.

```yaml
version: 1
name: Organisation d'un événement
description: Tâches planifiées autour de la date de l'événement
end_date:
  required: true
  label: Date de l'événement
columns:
  - À faire
  - En cours
  - Terminé
tasks:
  - title: Réserver la salle
    due: -30d
    priority: high
    column: À faire
    description: Vérifier la capacité et l'accès
    labels: [logistique]
  - title: Envoyer les convocations
    due: -7d
    priority: medium
  - title: Préparer l'ordre du jour
    due: -5b
  - title: Rédiger le compte rendu
    due: +3d
    subtasks:
      - title: Relire le brouillon
        due: +5d
      - title: Diffuser
  - title: Faire le point avec l'équipe
```

Champs :

- `version` : entier, `1` pour cette spécification. Un modèle avec une version inconnue est refusé.
- `name` : requis. `description` : facultatif.
- `end_date` : facultatif. Si absent, le champ date n'apparaît pas dans l'UI et aucune tâche ne peut avoir de `due`. `required: true` rend la saisie obligatoire. `required: false` laisse le choix : sans date, les tâches avec `due` sont créées sans échéance. `label` personnalise le libellé du champ.
- `columns` : facultatif. Présent : les colonnes sont créées dans le projet. Absent : les colonnes par défaut de Kaneo sont conservées. C'est ainsi que le choix est laissé à l'auteur du modèle.
- `tasks[]` :
  - `title` requis.
  - `description`, `priority` (valeurs de Kaneo), `column` (doit exister dans `columns` ou dans les colonnes du projet), `labels` : facultatifs.
  - `due` : décalage par rapport à la date de fin.
  - `subtasks[]` : mêmes champs, un seul niveau de profondeur.

Syntaxe de `due` :

- `-7d` : 7 jours calendaires avant la date de fin. `+3d` : 3 jours après. `0d` : le jour même.
- `-5b` / `+2b` : jours ouvrés (lundi à vendredi), sans jours fériés en v1.
- Les formes `J-7` et `J+3` sont acceptées comme alias.

Règles de calcul :

- La date calculée est une date sans heure. L'heure appliquée à l'échéance dans Kaneo est configurable plus tard ; en v1, midi UTC pour éviter les décalages de fuseau.
- Une date calculée dans le passé n'est pas bloquante : elle est signalée en avertissement dans la prévisualisation.
- Une tâche sans `due` est créée sans échéance.

---

## 6. Interface

Page unique `/`, rendue côté serveur, enrichie avec HTMX.

1. Sélecteur de workspace, alimenté par l'API. Le workspace de `KANEO_WORKSPACE_ID` est présélectionné s'il est défini.
2. Sélecteur de modèle, avec sa description.
3. Champ nom du projet.
4. Champ date de fin, affiché selon le modèle choisi, avec le libellé et le caractère obligatoire définis par le modèle.
5. Prévisualisation, mise à jour à chaque changement : liste des colonnes puis des tâches (sous-tâches indentées) avec leur échéance calculée, les jours de week-end et les dates passées signalés.
6. Bouton « Créer le projet ».
7. Résultat : lien vers le projet dans Kaneo, nombre de tâches créées, et en cas d'échec partiel la liste de ce qui a été créé et de ce qui a échoué.

Interface disponible en français et en anglais via un dictionnaire de traductions simple. Accessible au clavier, utilisable sur mobile.

---

## 7. Flux de création

1. Valider les entrées côté serveur : workspace, modèle existant (identifiant pris dans la liste chargée, jamais dans un chemin fourni par le client), nom non vide, date présente si requise.
2. Calculer toutes les échéances (fonction pure, testée seule).
3. Créer le projet dans le workspace.
4. Si le modèle définit `columns`, créer les colonnes ; sinon lire celles du projet.
5. Résoudre les `labels` : récupérer les labels du workspace, créer ceux qui manquent.
6. Créer les tâches dans l'ordre du modèle, dans leur colonne (la première colonne par défaut).
7. Créer les sous-tâches et les relier à leur tâche parente.
8. Retourner un rapport : éléments créés, avertissements, échecs.

En cas d'erreur à mi-parcours, rien n'est supprimé : le projet reste en place et le rapport indique où l'opération s'est arrêtée. Pas de nouvelle tentative automatique des écritures, pour ne pas créer de doublons ; les lectures peuvent être retentées une fois.

---

## 8. Point à vérifier en premier : l'API réelle de Kaneo

Avant d'écrire le client, lire l'OpenAPI de l'instance cible (`apps/docs/openapi.json` dans le dépôt Kaneo ou la doc de l'API) et consigner dans `docs/kaneo-api-notes.md` :

- en-tête d'authentification attendu pour un token, et manière de générer un token ;
- liste des workspaces ;
- création de projet : champs requis, rattachement au workspace ;
- colonnes : liste, création, lien avec le champ statut des tâches ;
- création de tâche : champs (titre, description, priorité, statut ou colonne, échéance, assigné, labels) et format de la date d'échéance ;
- labels : liste et création au niveau du workspace, rattachement à une tâche ;
- sous-tâches : mécanisme exact (relation parent/enfant) ;
- codes d'erreur et format des messages ;
- device flow : format exact des réponses de `/api/auth/device/code` et `/api/auth/device/token` (succès, `authorization_pending`, `slow_down`, `access_denied`, `expired_token`), durée de vie du token obtenu, existence d'un refresh token (référence : `packages/mcp/src/auth/device-flow.ts` dans le dépôt `usekaneo/kaneo`).

Si un point de ce document contredit la spec (par exemple pas de relation de sous-tâche, ou pas de création de colonne), mettre à jour la spec avant de continuer et signaler l'écart. La version de Kaneo contre laquelle le module a été testé est indiquée dans le README.

---

## 9. Structure du dépôt

```
kaneo-templates/
  src/kaneo_templates/
    main.py
    config.py
    auth.py
    kaneo_client.py
    schema.py
    loader.py
    scheduling.py
    creator.py
    i18n.py
    web/
      routes.py
      templates/
      static/
  templates-examples/
    evenement.yml
    reunion-ca.yml
    onboarding.yml
  tests/
  docs/
    kaneo-api-notes.md
    templates.md
    deployment.md
  Dockerfile
  docker-compose.example.yml
  .env.example
  pyproject.toml
  README.md
  LICENSE
  SPEC.md
  .github/workflows/ci.yml
```

---

## 10. Tests

- `scheduling` : décalages positifs et négatifs, passage de mois et d'année, années bissextiles, jours ouvrés avec départ un week-end, alias `J-7`.
- `schema` / `loader` : modèle valide, version inconnue, colonne inexistante, `due` sans `end_date`, YAML cassé, doublons de titres tolérés.
- `kaneo_client` : succès et erreurs avec respx, aucune requête réelle.
- `creator` : ordre des appels, rapport en cas d'échec partiel, absence de nouvelle tentative sur les écritures.
- Routes : CSRF, mode `user` sans cookie, mode `service` avec et sans Basic Auth, traversée de chemin sur l'identifiant de modèle.
- Test d'intégration facultatif, marqué à part, contre un Kaneo lancé par `docker compose` avec un projet jetable.

---

## 11. Distribution

- CI GitHub Actions : ruff, pytest, build de l'image.
- Image publiée sur GHCR, tags `latest` et par version.
- `docker-compose.example.yml` avec port bindé sur une IP configurable, volume `./templates:/data/templates:ro`, sans commentaires superflus.
- README en anglais avec une version française : prérequis, configuration, modes d'authentification, exemple de reverse proxy, format des modèles, limites connues.

---

## 12. Étapes de développement

1. Vérification de l'API Kaneo et `docs/kaneo-api-notes.md` (§8).
2. Squelette du projet, configuration, CI, Dockerfile.
3. `scheduling` et `schema`/`loader` avec leurs tests.
4. `kaneo_client` et test manuel contre l'instance avec un projet jetable.
5. `creator` avec rapport d'erreurs.
6. Authentification (modes `service` et `user`, Basic Auth, CSRF).
7. Interface : formulaire, prévisualisation HTMX, résultat, i18n.
8. Documentation, exemples de modèles, publication de l'image.

Critère de fin de v1 : depuis une instance Kaneo vierge, un utilisateur lance le conteneur, choisit un workspace et le modèle d'exemple `evenement`, saisit une date de fin, et obtient un projet avec ses colonnes, ses tâches aux bonnes échéances (J-30, J-7, J+3) et ses sous-tâches reliées.

---

## 13. Pour plus tard

- Éditeur de modèles dans l'UI, import et export de modèles.
- Variables dans les titres et descriptions (`{{project_name}}`, `{{end_date}}`).
- Jours fériés par pays pour les jours ouvrés.
- Date de début de projet comme second point d'ancrage.
- Attribution des tâches par rôle, associée à des membres choisis à la création.
- Connexion OIDC directe.
- Création depuis un projet existant (« enregistrer comme modèle »).

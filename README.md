# LittlePrincess

Un bot Discord pour la guilde, inspiré de **Guardian Tales** : invocations, collection de héros et d'armes,
progression par l'activité, wordle de héros, duels au colisée, boss du samedi, dummy, boutique et succès.

> Données des héros : **[Guardian Tales TOP](https://gtales.top)** (merci à eux !).
> Projet de fans, non affilié à Kakao Games / Kong Studios.

---

## Sommaire

1. [Fonctionnalités](#fonctionnalités)
2. [Créer le bot sur le Discord Developer Portal](#1-créer-le-bot-sur-le-discord-developer-portal)
3. [Obtenir le token de l'API gtales.top](#2-obtenir-le-token-de-lapi-gtalestop)
4. [Publier l'image Docker](#3-publier-limage-docker-github)
5. [Installer le bot sur le NAS](#4-installer-le-bot-sur-le-nas)
6. [Configurer le jeu](#5-configurer-le-jeu-configconfigyaml)
7. [Mettre à jour le bot](#6-mettre-à-jour-le-bot-bot-update)
8. [Commandes](#commandes)
9. [Règles du jeu](#règles-du-jeu)
10. [Sauvegarde et dépannage](#sauvegarde)

---

## Fonctionnalités

| | |
|---|---|
| 🎰 **Gacha** | Bannière standard, bannière vedette (change chaque semaine), bannière d'armes. Taux officiels de GT, 3★ (ou arme 5★) garanti au bout de 80 invocations. |
| 🦸 **Collection** | Un doublon = +1★ (jusqu'à 5★, au-delà converti en un peu de gemmes et d'or). Niveaux achetés avec de l'or, niveau max fixé par les étoiles. Armes génériques par type + armes exclusives 5★. |
| 👥 **Équipe** | 4 héros, le 1er est le leader (son buff d'équipe est renforcé). |
| 📅 **Quotidien** | `/daily` avec série sur 7 jours, 3 missions quotidiennes et 3 hebdomadaires. |
| 📈 **Progression** | XP par message (anti-spam), par réaction et via les mini-jeux. Gemmes et or à chaque niveau, rôles automatiques, titres, annonce. |
| 🧩 **Wordle** | Façon Loldle : un héros du jour commun à tous, indices par caractéristique, réponses privées. |
| ⚔️ **Colisée** | Duel en direct contre un membre, combat simulé automatiquement, mise de gemmes. |
| 👹 **Boss** | Chaque samedi à 18h : raid en direct au tour par tour (boutons), puis rattrapage solo jusqu'au dimanche soir. PV adaptés au niveau de la guilde. |
| 🎯 **Dummy** | Teste les dégâts de ton équipe ou d'un héros, détail par compétence, classement. |
| 🛒 **Divers** | Boutique, dons plafonnés, succès, titres, salons dédiés, `/bot update` avec annonce des nouveautés. |

---

## 1. Créer le bot sur le Discord Developer Portal

1. Va sur <https://discord.com/developers/applications> → **New Application** → nomme-la (ex. *LittlePrincess*).
2. Onglet **Installation** : mets **Install Link** sur **None** (sinon l'étape 3 refuse de décocher *Public Bot*).
3. Onglet **Bot** :
   - **Reset Token** → copie le token : c'est le mot de passe du bot, il ira dans `.env` (`DISCORD_TOKEN`). Ne le partage jamais.
   - Décoche **Public Bot** (personne d'autre ne pourra l'ajouter).
   - **Privileged Gateway Intents** : active **Server Members Intent**. Les deux autres (*Presence*, *Message Content*) restent désactivés : le bot n'utilise que des commandes `/`.
4. Onglet **OAuth2** → **URL Generator** :
   - Scopes : `bot` et `applications.commands`
   - Permissions : *View Channels*, *Send Messages*, *Send Messages in Threads*, *Create Public Threads*,
     *Embed Links*, *Attach Files*, *Read Message History*, *Add Reactions*, *Use External Emojis*,
     *Manage Roles* (rôles de niveau), *Mention Everyone* (mention du rôle boss)
   - Ouvre le lien généré en bas de page et choisis ton serveur.
5. Sur ton serveur : **Paramètres du serveur → Rôles**, glisse le rôle du bot **au-dessus** des rôles de niveau qu'il doit donner.
6. Récupère l'**ID du serveur** : Paramètres Discord → Avancés → **Mode développeur** ; puis clic droit sur le serveur → *Copier l'identifiant du serveur* (`GUILD_ID` dans `.env`). Grâce à lui, les commandes apparaissent immédiatement.

## 2. Obtenir le token de l'API gtales.top

L'API de [Guardian Tales TOP](https://gtales.top/api) demande un token (gratuit) pour les bots Discord :
rejoins leur Discord (lien sur la page API) et ouvre un ticket. Mets ensuite le token dans `.env` (`GTALES_API_TOKEN`).

- **Sans token**, le bot fonctionne avec une vingtaine de héros de démarrage aux stats **approximatives**.
- **Dès que le token est ajouté** (puis le bot redémarré), il télécharge tous les héros, les met en cache
  (`data/heroes_cache.json`, rafraîchi chaque semaine) et **convertit automatiquement** les héros de démarrage
  déjà invoqués vers les vrais (même nom). `/bot sync` force une resynchronisation.
- Le site demande de citer la source : c'est fait dans les fiches de héros, `/aide` et `/bot version`.

## 3. Publier l'image Docker (GitHub)

L'image est construite automatiquement par **GitHub Actions** (`.github/workflows/docker.yml`) à chaque push sur
`main`, pour PC/serveur (amd64) et ARM (arm64), et publiée sur `ghcr.io/shiro645/littleprincess:latest`.

1. Fusionne la branche de développement dans `main` (pull request) : l'action lance les tests puis construit l'image (≈ 5 min, onglet **Actions**).
2. **Une seule fois** : rends l'image publique pour que le NAS puisse la télécharger sans identifiants.
   GitHub → ton profil → **Packages** → `littleprincess` → **Package settings** → *Change visibility* → **Public**.

## 4. Installer le bot sur le NAS

Il faut 3 fichiers dans un dossier du NAS (ex. `/volume1/docker/littleprincess`) : `docker-compose.yml`,
`.env` (copié depuis `.env.example`) et c'est tout. Les dossiers `config/` et `data/` sont créés au démarrage.

### Remplir le fichier `.env`

```env
DISCORD_TOKEN=le-token-du-bot
GUILD_ID=123456789012345678
GTALES_API_TOKEN=le-token-gtales (facultatif au début)
WATCHTOWER_TOKEN=une-longue-chaine-aleatoire
```

### Option A — Synology (Container Manager)

1. **File Station** : crée `docker/littleprincess` et dépose-y `docker-compose.yml` et `.env`.
2. **Container Manager → Projet → Créer** : nom `littleprincess`, chemin = ce dossier, source = *utiliser le docker-compose.yml existant*.
3. Valide : Container Manager télécharge les images et lance les 2 containers (`littleprincess` et `littleprincess-watchtower`).
4. Logs : **Container → littleprincess → Journal**. Tu dois voir `Connecté en tant que …`.

> Sur un autre NAS (QNAP, Unraid, Portainer…), le principe est le même : créer une « stack » à partir du `docker-compose.yml` avec le `.env` à côté.

### Option B — En ligne de commande (SSH)

```bash
mkdir -p /volume1/docker/littleprincess && cd /volume1/docker/littleprincess
# récupère les deux fichiers depuis GitHub
curl -O https://raw.githubusercontent.com/Shiro645/LittlePrincess/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/Shiro645/LittlePrincess/main/.env.example
nano .env                      # remplis les valeurs
docker compose up -d           # télécharge et démarre
docker compose logs -f bot     # suivre les logs (Ctrl+C pour quitter)
```

Commandes utiles :

| Action | Commande |
|---|---|
| Arrêter | `docker compose down` |
| Redémarrer (après modif de `.env`) | `docker compose up -d` |
| Redémarrer (après modif de `config.yaml`) | `docker compose restart bot` ou `/bot recharger` sur Discord |
| Mettre à jour à la main | `docker compose pull && docker compose up -d` |

### Construire l'image soi-même (sans GitHub)

Clone le dépôt sur le NAS, puis dans `docker-compose.yml` remplace `image: …` par `build: .` et lance
`docker compose up -d --build`. (`/bot update` ne fonctionne alors pas : mets à jour avec `git pull` + la même commande.)

## 5. Configurer le jeu (`config/config.yaml`)

Au premier démarrage, le bot crée `config/config.yaml` avec **toutes les valeurs du jeu**, commentées en français :
taux, coûts, pitié, XP, récompenses, missions, boss, boutique, succès… Après une modification : `/bot recharger`.

À régler en priorité (IDs obtenus par clic droit → *Copier l'identifiant* en mode développeur) :

```yaml
channels:
  announcements: 111111111111111111   # nouveautés après chaque mise à jour
  admin: 222222222222222222           # alertes staff (nouvelle version dispo…)
  level_up: 333333333333333333        # annonces de niveau
  boss: 444444444444444444            # salon du boss du samedi
  allowed: []                         # salons autorisés pour les commandes (vide = partout)
roles:
  staff: [555555555555555555]         # accès à /admin et /bot (les administrateurs l'ont toujours)
  boss_ping: 666666666666666666       # rôle mentionné quand le boss apparaît
leveling:
  level_roles:                        # rôles automatiques par niveau
    10: 777777777777777777
    25: 888888888888888888
```

Lors d'une mise à jour du bot, les **nouvelles options** sont ajoutées automatiquement avec leur valeur par
défaut : ton fichier n'est jamais écrasé.

## 6. Mettre à jour le bot (`/bot update`)

1. Le code est modifié (par exemple par Claude) et fusionné dans `main` : GitHub construit la nouvelle image.
2. Le bot vérifie chaque jour s'il existe une nouvelle version et **prévient le staff** dans `channels.admin`.
3. Un membre du staff tape **`/bot update`** : Watchtower télécharge la nouvelle image et redémarre le bot.
4. Au redémarrage, le bot **annonce les nouveautés** (section du `CHANGELOG.md`) dans `channels.announcements`.

Pour publier une version : augmenter `__version__` dans `bot/__init__.py` et ajouter une section en haut de `CHANGELOG.md`.

> Watchtower a besoin d'accéder au socket Docker du NAS (`/var/run/docker.sock`) : il ne met à jour **que** le
> container du bot (label `watchtower.enable`), et **uniquement** quand `/bot update` le demande avec le
> `WATCHTOWER_TOKEN`. Le projet Watchtower d'origine n'étant plus maintenu, on utilise le fork
> [`nickfedor/watchtower`](https://github.com/nicholas-fedor/watchtower).

---

## Commandes

| Commande | Description |
|---|---|
| `/aide` | Liste des commandes |
| `/profil [membre]` | Niveau, monnaies, collection, équipe |
| `/classement <catégorie>` | Niveau, collection, duels, dummy, boss |
| `/invoquer <bannière> <x1/x10> [ticket]` | Invocations |
| `/bannieres` | Bannières actuelles, taux, ta pitié |
| `/heros liste·info·ameliorer` | Tes héros |
| `/arme liste·info·equiper·retirer·ameliorer` | Tes armes |
| `/team voir·definir` | Ton équipe (le 1er = leader) |
| `/daily` · `/missions` | Récompense du jour, missions |
| `/wordle deviner·classement` | Héros du jour |
| `/colisee defier·stats` | Duels avec mise |
| `/boss info·solo·classement` | Boss du samedi |
| `/dummy tester·classement` | Mannequin d'entraînement |
| `/boutique voir·acheter` · `/donner` | Boutique et dons |
| `/succes` · `/titre` | Succès et titres |
| `/bot version·verifier·update·sync·recharger` | **Staff** : gestion du bot |
| `/admin donner·retirer·heros·reset·boss-lancer·boss-terminer·annonce` | **Staff** : gestion des joueurs |

## Règles du jeu

- **Stats** : celles de l'API correspondent à un héros niveau 100, 5★. Un héros niveau L avec E étoiles a
  `stat × facteur_niveau(L) × facteur_étoiles(E)` (de 12 % au niveau 1 / 1★ à 100 %).
- **Combat** : chaque tour, chaque héros attaque ou lance sa compétence d'arme (recharge en tours). Les compétences
  infligent des états (*À terre*, *Projeté*, *Blessé*) qui déclenchent les **compétences de chaîne** des alliés
  (combo : +10 % par maillon). Avantage élémentaire ×1,3 (Feu > Terre > Eau > Feu, Lumière ⇄ Ténèbres).
  Les tanks attirent les coups, les soutiens soignent, les buffs d'équipe s'appliquent (celui du leader ×1,5).
- **Boss** : chaque participant ajoute des PV au boss selon la puissance de son équipe (mode `adaptive`) ; le boss
  inflige des dégâts en % des PV des héros. Actions par tour : ⚔️ Attaque, ✨ Compétence, 🛡️ Garde (−50 % de dégâts).
  Récompenses : participation + bonus de rang aux dégâts + bonus si le boss est vaincu, distribuées dimanche soir.

## Sauvegarde

Tout l'état du jeu est dans le dossier `data/` (fichier `littleprincess.db`). Pour sauvegarder : copier ce dossier
(idéalement bot arrêté, ou avec la tâche de sauvegarde du NAS). `config/` contient ta configuration.

## Dépannage

| Problème | Solution |
|---|---|
| `PrivilegedIntentsRequired` dans les logs | Active **Server Members Intent** (étape 1.3). |
| Les commandes `/` n'apparaissent pas | Vérifie `GUILD_ID` ; réinvite le bot avec le scope `applications.commands`. |
| Les rôles de niveau ne sont pas donnés | Permission *Gérer les rôles* + rôle du bot au-dessus des rôles de niveau. |
| `/bot update` : « Watchtower injoignable » | Le container `littleprincess-watchtower` doit tourner (même `docker-compose.yml`). |
| `/bot update` : HTTP 401 | `WATCHTOWER_TOKEN` différent entre le bot et Watchtower : relance `docker compose up -d`. |
| `denied` en téléchargeant l'image | L'image GHCR n'est pas publique (étape 3.2). |
| « données provisoires » sur les héros | Ajoute `GTALES_API_TOKEN` dans `.env` puis `docker compose up -d`. |

## Développement

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v     # tests
python -m bot                               # lancer le bot (variables de .env exportées)
```

Structure : `bot/game/` (règles pures : combat, gacha, stats), `bot/services/` (base de données),
`bot/cogs/` (commandes Discord), `config/config.yaml` (valeurs par défaut), `tests/`.

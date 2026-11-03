# LittlePrincess

A Discord bot for a **Guardian Tales** guild: summons, hero and weapon collection, activity-based progression,
a hero wordle, colosseum duels, a Saturday raid boss, a training dummy, a shop and achievements.

The bot talks to players in **French**; the code and this documentation are in English.

> Hero data: **[Guardian Tales TOP](https://gtales.top)** (thanks to them!).
> Fan project, not affiliated with Kakao Games / Kong Studios.

---

## Contents

1. [Features](#features)
2. [Create the bot on the Discord Developer Portal](#1-create-the-bot-on-the-discord-developer-portal)
3. [Get a gtales.top API token](#2-get-a-gtalestop-api-token)
4. [Publish the Docker image](#3-publish-the-docker-image-github)
5. [Install the bot on a NAS](#4-install-the-bot-on-a-nas)
6. [Configure the game](#5-configure-the-game-configuration-panel)
7. [Update the bot](#6-update-the-bot-bot-update)
8. [Commands](#commands)
9. [Game rules](#game-rules)
10. [Backups, troubleshooting, development](#backups)

---

## Features

| | |
|---|---|
| 🎰 **Gacha** | Standard banner, weekly featured banner, weapon banner. Official GT rates; a 3★ (or 5★ weapon) is guaranteed after 80 summons. |
| 🦸 **Collection** | A duplicate gives +1★ (up to 5★, then converted into a few gems and gold). Levels are bought with gold, the level cap depends on stars. Generic weapons per type + 5★ exclusive weapons. |
| 👥 **Team** | 4 heroes; the first one is the leader (their party buff is boosted). |
| 📅 **Daily** | `/daily` with a 7-day streak, 3 daily and 3 weekly missions. |
| 📈 **Progression** | XP per message (anti-spam), per reaction and from mini-games. Gems and gold on level-up, automatic roles, titles, announcements. |
| 🧩 **Wordle** | Loldle style: one hero of the day for everyone, per-attribute hints, private answers. |
| ⚔️ **Colosseum** | Live duel against a member, automatically simulated fight, gem bets. |
| 👹 **Boss** | Every Saturday at 18:00: live turn-based raid (buttons), then a solo catch-up until Sunday night. HP adapts to the guild's strength. |
| 🎯 **Dummy** | Test the damage of your team or of one hero, per-skill breakdown, leaderboard. |
| ⚙️ **Configuration panel** | Every game value lives in one `config.json`, edited from an interactive panel that only the staff member sees. |
| 🛒 **Misc** | Shop, capped gifts, achievements, titles, dedicated channels, `/bot update` with a changelog announcement. |

---

## 1. Create the bot on the Discord Developer Portal

1. Go to <https://discord.com/developers/applications> → **New Application** → name it (e.g. *LittlePrincess*).
2. **Installation** tab: set **Install Link** to **None** (otherwise step 3 refuses to untick *Public Bot*).
3. **Bot** tab:
   - **Reset Token** → copy the token: it is the bot's password and goes into `DISCORD_TOKEN`. Never share it.
   - Untick **Public Bot** (nobody else can add it).
   - **Privileged Gateway Intents**: enable **Server Members Intent**, then **Save Changes**. Leave *Presence* and *Message Content* disabled: the bot only uses slash commands.
4. Invite the bot with this link (replace `APPLICATION_ID` with the ID shown in **General Information**):

   ```
   https://discord.com/oauth2/authorize?client_id=APPLICATION_ID&permissions=309506198528&integration_type=0&scope=bot+applications.commands
   ```

   It grants: View Channels, Send Messages, Embed Links, Attach Files, Read Message History, Create Public Threads,
   Send Messages in Threads and Manage Roles (level roles).
5. On the server: **Server Settings → Roles**, drag the bot's role **above** the level roles it must give.
   For the boss ping, enable *Allow anyone to @mention this role* on the chosen role (or use
   `permissions=309506329600` to also grant *Mention Everyone*).
6. Copy the **server ID** (Discord settings → Advanced → **Developer Mode**, then right-click the server →
   *Copy Server ID*) into `GUILD_ID`. With it, commands show up instantly.

## 2. Get a gtales.top API token

The [Guardian Tales TOP API](https://gtales.top/api) requires a (free) token for Discord bots: join their Discord
(link on the API page) and open a ticket. Then set `GTALES_API_TOKEN`.

- **Without a token**, the bot runs with about twenty seed heroes with **approximate** stats.
- **Once the token is set** (and the bot restarted), it downloads every hero, caches them
  (`data/heroes_cache.json`, refreshed weekly) and **automatically converts** seed heroes players already own
  into the real ones (matched by name). `/bot sync` forces a resync.
- The site asks to credit the source: it is shown on hero cards, `/aide` and `/bot version`.

## 3. Publish the Docker image (GitHub)

**GitHub Actions** (`.github/workflows/docker.yml`) runs the tests on every push and builds the image on every
push to `main`, for amd64 and arm64, published as `ghcr.io/shiro645/littleprincess:latest`.

1. Merge the pull request into `main`: the workflow tests and builds the image (≈ 2–5 min, **Actions** tab).
2. **Once**: make the package public so the NAS can pull it without credentials.
   GitHub → your profile → **Packages** → `littleprincess` → **Package settings** → *Change visibility* → **Public**.

## 4. Install the bot on a NAS

The stack has two containers: **littleprincess** (the bot) and **littleprincess-watchtower** (pulls new images
when `/bot update` is used). The bot stores its files next to the compose file: `config/` (config.json) and
`data/` (SQLite database + hero cache).

### Environment variables

| Variable | Value |
|---|---|
| `DISCORD_TOKEN` | Bot token (step 1.3) |
| `GUILD_ID` | Server ID (step 1.6) |
| `GTALES_API_TOKEN` | gtales.top token (optional at first) |
| `WATCHTOWER_TOKEN` | Any long random string. It must be identical to `WATCHTOWER_HTTP_API_TOKEN` of the Watchtower container |

### Option A — NAS web interface (UGREEN UGOS Pro, Synology, QNAP…)

1. Install the **Docker** app (UGOS Pro: App Center; Synology: Container Manager).
2. Create a folder for the bot, e.g. `docker/littleprincess`.
3. **Docker → Project → Create**: name `littleprincess`, storage path = that folder, and paste `docker-compose.yml`.
   Web editors make dot-files awkward, so instead of `env_file: .env` you can put the variables directly under
   `environment:` of the `bot` service (and the same token under `WATCHTOWER_HTTP_API_TOKEN`).
4. Deploy, then open **Container → littleprincess → Logs**: you should see `Logged in as …`.

### Option B — Command line (SSH)

```bash
mkdir -p /volume1/docker/littleprincess && cd /volume1/docker/littleprincess
curl -O https://raw.githubusercontent.com/Shiro645/LittlePrincess/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/Shiro645/LittlePrincess/main/.env.example
nano .env                      # fill in the values
docker compose up -d           # pull and start
docker compose logs -f bot     # follow the logs (Ctrl+C to quit)
```

| Action | Command |
|---|---|
| Stop | `docker compose down` |
| Apply a `.env` change | `docker compose up -d` |
| Apply a manual `config.json` edit | **Reload** button on the config panel (or `/bot recharger`) |
| Update manually | `docker compose pull && docker compose up -d` |

### Building the image yourself

Clone the repository, replace `image: …` with `build: .` in `docker-compose.yml` and run
`docker compose up -d --build`. `/bot update` does not work in that mode: update with `git pull` + the same command.

## 5. Configure the game (configuration panel)

Every game value lives in **one file**, `config/config.json`: rates, costs, pity, XP, rewards, missions, boss,
shop, achievements, channels, roles… It is created on first start and edited **from Discord**:

1. A staff member runs **`/config panneau`**. The panel opens as an **ephemeral message**: only they see it,
   nothing is posted in the channel, and it disappears when dismissed.
2. Pick a section in the menu. The same message turns into a browser:
   - 🔘 **booleans** toggle with one click;
   - #️⃣ 👥 **channels and roles** use Discord's channel/role pickers (no IDs to copy);
   - ✏️ **other values** open a form pre-filled with the current value; 📁 sub-sections open in place;
   - buttons: **Back** (up to the section list), **Edit as JSON**, **Add an item** (lists), **Add a tier** (level roles),
     **Delete this item**, **Reset** (two clicks).
3. The panel also has **Export** (download `config.json`) and **Reload file** (after a manual edit).
   `/config importer` uploads a whole `config.json`.

Every change is **validated** (types, rates summing to 100 %, valid timezone, complete bosses, unique ids…), then
**applied immediately** without a restart. The previous version is kept in `config.json.bak` and the change is
logged in the staff channel. If the file is broken by hand, the bot starts with default values and warns the staff.
New options added by an update are merged in automatically; an old `config.yaml` (v1.0.0) is converted once.

Secrets (Discord token, API token, Watchtower token) stay in environment variables and are deliberately **not**
editable from Discord.

Setting descriptions shown in the panel live in `bot/data/config_help.json` (in French, since staff read them).

## 6. Update the bot (`/bot update`)

1. Changes are merged into `main`; GitHub builds the new image.
2. The bot checks for a new version daily and **warns the staff** in `channels.admin`.
3. A staff member runs **`/bot update`**: Watchtower pulls the new image and restarts the bot.
4. On restart, the bot **announces the changes** (the matching `CHANGELOG.md` section) in `channels.announcements`.

To release a version: bump `__version__` in `bot/__init__.py` and add a section at the top of `CHANGELOG.md`.
Changelog entries are written in French because they are posted to players.

> Watchtower needs the NAS Docker socket (`/var/run/docker.sock`). It only updates the bot container (label
> `watchtower.enable`) and only when `/bot update` asks for it with `WATCHTOWER_TOKEN`. The original Watchtower
> project is no longer maintained, so the stack uses the [`nickfedor/watchtower`](https://github.com/nicholas-fedor/watchtower) fork.

---

## Commands

Slash commands are in French (they are what players type).

| Command | Description |
|---|---|
| `/aide` | Command list |
| `/profil [membre]` | Level, currencies, collection, team |
| `/classement <catégorie>` | Level, collection, duels, dummy, boss |
| `/invoquer <bannière> <x1/x10> [ticket]` | Summon |
| `/bannieres` | Current banners, rates, pity |
| `/heros liste·info·ameliorer` | Heroes |
| `/arme liste·info·equiper·retirer·ameliorer` | Weapons |
| `/team voir·definir` | Team (first hero = leader) |
| `/daily` · `/missions` | Daily reward, missions |
| `/wordle deviner·classement` | Hero of the day |
| `/colisee defier·stats` | Duels with bets |
| `/boss info·solo·classement` | Saturday boss |
| `/dummy tester·classement` | Training dummy |
| `/boutique voir·acheter` · `/donner` | Shop and gifts |
| `/succes` · `/titre` | Achievements and titles |
| `/config panneau·exporter·importer` | **Staff**: configuration panel |
| `/bot version·verifier·update·sync·commandes·recharger` | **Staff**: bot management |
| `/admin donner·retirer·heros·reset·boss-lancer·boss-terminer·annonce` | **Staff**: player management |

## Game rules

- **Stats**: API stats are for a level 100, 5★ hero. A level L hero with S stars has
  `stat × level_factor(L) × star_factor(S)` (from 12 % at level 1 / 1★ up to 100 %).
- **Combat**: each round, every hero attacks or uses their weapon skill (cooldown in rounds). Skills apply states
  (*Downed*, *Airborne*, *Injured*) that trigger allies' **chain skills** (+10 % per chain link). Element
  advantage ×1.3 (Fire > Earth > Water > Fire, Light ⇄ Dark). Tanks draw aggro, supports heal, party buffs apply
  (the leader's ×1.5).
- **Boss**: each participant adds HP to the boss based on their team's strength (`adaptive` mode); the boss deals
  damage as a % of heroes' HP. Actions per turn: ⚔️ Attack, ✨ Skill, 🛡️ Guard (−50 % damage). Rewards:
  participation + damage rank bonus + kill bonus, handed out on Sunday night.

## Backups

All game state is in `data/` (`littleprincess.db`). Back up that folder (ideally with the bot stopped, or with the
NAS backup task). `config/` holds the configuration.

## Troubleshooting

| Problem | Fix |
|---|---|
| `PrivilegedIntentsRequired` in the logs | Enable **Server Members Intent** and click **Save Changes** (step 1.3). |
| `invalid literal for int()` | `GUILD_ID` was not replaced with the server ID. |
| Slash commands don't show up | Check `GUILD_ID`; re-invite the bot with the `applications.commands` scope. |
| An old bot's commands show up | Same Discord application: the bot removes them on startup (or `/bot commandes`), then Ctrl+R in Discord. Another bot still on the server: kick it. |
| Level roles are not given | *Manage Roles* permission + bot role above the level roles. |
| `/bot update`: "Watchtower injoignable" | The `littleprincess-watchtower` container must be running (same compose project). |
| `/bot update`: HTTP 401 | `WATCHTOWER_TOKEN` differs between the bot and Watchtower. |
| `denied` when pulling the image | The GHCR package is not public (step 3.2). |
| "données provisoires" on heroes | Set `GTALES_API_TOKEN` and restart. |

## Development

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v     # tests (Discord tests need discord.py)
python -m bot                               # run the bot (with the environment variables exported)
```

Layout:

| Path | Content |
|---|---|
| `bot/game/` | Pure game rules: combat engine, gacha, stats, wordle, simulations |
| `bot/services/` | Database-backed services: players, collection, progress, leveling |
| `bot/cogs/` | Discord commands |
| `bot/ui/` | Embeds, paginators, battle display, configuration panel |
| `bot/config.py` | config.json loading, merging, paths, validation |
| `config/config.json` | Default configuration |
| `bot/data/` | Seed heroes and setting descriptions |
| `tests/` | Unit tests + Discord smoke tests |

Conventions: code, comments, logs and docs in English; everything players and staff see on Discord in French.

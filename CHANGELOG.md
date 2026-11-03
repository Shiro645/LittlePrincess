# Changelog

<!-- Each section is posted on Discord (channels.announcements) after an update, so entries are
written in French for the players. To release: bump __version__ in bot/__init__.py and add a
section at the top of this file. -->

## [1.2.0] - Panneau de configuration
- ⚙️ **Nouveau panneau de configuration** : `/config panneau` publie un message interactif pour régler tout le bot (menus, boutons, formulaires)
- #️⃣ Les salons et les rôles se choisissent directement dans les menus de Discord, plus besoin de copier des identifiants
- 🔘 Les options oui/non s'activent en un clic, les listes (boutique, boss, succès…) peuvent être complétées depuis le panneau
- 🧹 Les anciennes commandes `/config voir`, `modifier`, `salon`… sont remplacées par le panneau (`exporter` et `importer` restent)

## [1.1.1] - Correctif des commandes
- 🧹 Les anciennes commandes `/` d'un précédent bot (même application Discord) sont supprimées au démarrage
- 🔄 Nouvelle commande staff `/bot commandes` pour resynchroniser les commandes sans redémarrer

## [1.1.0] - Configuration depuis Discord
- ⚙️ Toutes les valeurs du jeu sont maintenant dans un seul fichier **`config.json`**
- 🛠️ Le staff peut tout régler **directement depuis Discord** avec `/config` : voir, modifier, réinitialiser, exporter et importer la configuration
- 📍 Raccourcis pour choisir les salons du bot, les rôles staff, le rôle du boss et les rôles de niveau
- ✅ Chaque modification est vérifiée et appliquée tout de suite, sans redémarrer le bot
- 🔄 L'ancien `config.yaml` est converti automatiquement

## [1.0.0] - Lancement
- 🎰 **Invocations** de héros et d'armes (`/invoquer`, `/bannieres`) : taux officiels, bannière vedette hebdomadaire, 3★ garanti (pitié)
- 🦸 **Collection** : doublon = +1★, niveaux achetés avec de l'or, armes génériques et exclusives (`/heros`, `/arme`)
- 👥 **Équipes** de 4 héros avec leader et buffs d'équipe (`/team`)
- 📅 `/daily` avec série, **missions** quotidiennes et hebdomadaires (`/missions`)
- 📈 **XP d'activité** (messages, réactions, mini-jeux), récompenses de niveau, rôles et titres
- 🧩 **Wordle des héros** façon Loldle (`/wordle deviner`)
- ⚔️ **Colisée** : duels avec mise de gemmes (`/colisee defier`)
- 👹 **Boss du samedi 18h** en direct au tour par tour + rattrapage solo (`/boss`)
- 🎯 **Dummy** pour tester ses dégâts (`/dummy tester`)
- 🛒 Boutique, 🎁 dons, 🏆 succès et titres

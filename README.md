# Likes des amis Hevy — configuration sur iPhone

Version personnelle basee sur le depot MIT de dizzyfrogs. Le nouveau programme
`friends_bot.py` utilise les seances des personnes deja suivies. `social_bot.py`
ajoute les abonnements limites depuis les recommandations de Hevy. Aucun des
deux n'utilise les anciens modules follow/unfollow du programme d'origine.

## Abonnements automatiques

Le passage horaire lance aussi les nouveaux abonnements : maximum 2 par passage
et 20 tentatives sur 24 heures glissantes, parmi les utilisateurs recommandes
par Hevy. Ces plafonds sont choisis pour ce bot et ne representent pas une
autorisation ou une limite officielle de Hevy. Les comptes deja suivis, les
demandes en attente et les personnes deja tentees par le bot sont exclus.
Chaque tentative est chiffree et enregistree AVANT l'envoi pour eviter de
recommencer apres une coupure. Le statut following/requested est relu sur Hevy.
Le bot ne se desabonne de personne, ne publie pas de commentaires et ne change
pas la confidentialite du compte. Les abonnements recus ne sont pas garantis.

Mode manuel `automatique` : likes et nouveaux abonnements.
Mode `liker` : likes uniquement ; `verifier` et `controler` ne creent pas
d'abonnements. Les modifications du code ne font que des controles en lecture.
Variable `HEVY_FOLLOW_ENABLED=false` : suspend uniquement les nouveaux abonnements.
Variable `HEVY_ENABLED=false` : suspend tous les passages horaires.

L'acceptation automatique des demandes RECUES n'est pas activee : la route de
lecture est connue, mais la mutation d'acceptation n'a pas ete confirmee.
Le controle de deploiement lit uniquement leur nombre et la confidentialite
si ces informations sont disponibles. Aucun nom de demandeur n'est journalise.
Une API privee peut changer ou refuser une requete ; zero erreur et une
croissance constante des abonnes ne peuvent pas etre garantis.

## Etat actuel

Le workflow programme un passage de likes chaque heure lorsque le secret
HEVY_AUTH_JSON est renseigne. La premiere connexion doit etre validee en mode
verifier. L'API sociale utilisee n'est pas officielle et peut changer.

## Connexion depuis Safari

1. Connectez-vous a votre compte sur https://hevy.com dans Safari.
2. Creez un signet nomme « Connexion Hevy » pour cette page.
3. Modifiez l'adresse de ce signet pour y mettre toute la ligne du fichier
   `setup/connexion-safari.js`, y compris le debut `javascript:`.
4. Sur hevy.com, lancez ce signet. Il lit uniquement le cookie de connexion
   `auth2.0-token` du site. Il n'envoie rien sur le reseau. Si le cookie est
   inaccessible, il s'arrete : ne partagez pas d'autres cookies ni votre mot
   de passe. La disponibilite sur votre version Safari reste a confirmer.
5. Appuyez sur « Copier ma connexion ». Le texte copie contient une cle de
   connexion privee : ne pas l'envoyer dans le chat, un fichier du depot,
   une issue ou une capture d'ecran.
6. Dans votre depot GitHub : Settings > Secrets and variables > Actions >
   New repository secret. Nom : `HEVY_AUTH_JSON`. Collez le texte dans Secret.
   Puis Add secret. Effacez ensuite le presse-papiers en copiant un texte banal.

La connexion Safari et celle du bot partagent initialement une session. Pour
eviter des conflits de renouvellement, reservez cet onglet a la configuration ;
ne vous deconnectez pas de cette session apres copie. L'application iPhone
peut continuer a utiliser sa propre session. Si Hevy refuse la connexion,
refaire une liaison avec une connexion Safari recente.

## Premier essai sans likes

Une modification de `friends_bot.py` ou du workflow sur `main` controle jusqu'a
50 likes deja enregistres directement sur Hevy, sans envoyer de nouveaux likes.
Le mode manuel `controler` effectue le meme audit.


Actions > Likes des amis Hevy > Run workflow > mode `verifier` > Run workflow.
Sur un fork, GitHub peut demander d'abord l'activation d'Actions. Le mode
verifier lit les amis et les seances recentes, sans envoyer de likes. Les
journaux affichent seulement des compteurs, aucun jeton ni detail des seances.
Le profil attendu est `abakayhasan` ; si ce pseudo change, mettre a jour la
valeur HEVY_EXPECTED_USERNAME dans le workflow.

## Liker et activer les passages automatiques

Apres validation du premier essai, Run workflow avec le mode `liker` effectue
un passage immediat. Les passages horaires sont actives par defaut dans cette
version, avec un maximum de 100 likes par passage.

L'execution programmee est prevue chaque heure a la minute 23 en UTC ; GitHub
peut la retarder. Pour mettre en pause : Settings > Secrets and variables >
Actions > Variables > New repository variable : `HEVY_ENABLED` = `false`.
Pour reprendre : mettre cette variable a `true`, ou la supprimer. Le workflow
peut aussi etre desactive depuis Actions. Un lancement manuel reste possible
pendant cette pause.

## Perimetre et limites

- Seances des 7 derniers jours des personnes actuellement suivies.
- Collecte parmi tous les amis, puis traitement des publications les plus recentes
  en premier, au lieu d'un traitement par ordre alphabetique des amis.
- Verification de l'abonnement a la personne juste avant chaque like.
- Exclusion de ses propres seances et des seances deja likees.
- Chaque nouveau like est relu sur Hevy avant d'etre compte et enregistre.
- Maximum de 100 likes par passage, 40 pages de 5 seances par ami.
- Arret en cas de limite API, connexion refusee ou reponse non reconnue.
- Aucun abonnement, desabonnement, commentaire ni message automatique.

Le jeton initial reste dans un secret GitHub. Les jetons renouveles sont
conserves dans un cache chiffre avec Fernet ; la cle est derivee du secret
initial et n'est jamais placee dans ce cache. Aucune session en clair n'est
commitee ni televersee en artefact. Le cache peut etre evince par GitHub ;
dans ce cas une nouvelle liaison peut etre necessaire. Les workflows
programmes d'un depot public peuvent etre suspendus par GitHub apres une
periode d'inactivite. Le quota gratuit et les restrictions GitHub s'appliquent.

## Verification du code

Python 3.12 et `cryptography==50.0.2` :

```sh
python -m unittest discover -s tests -v
```

Les tests simulent Hevy. Ils ne valident pas le fonctionnement actuel de son
API sur un compte reel.

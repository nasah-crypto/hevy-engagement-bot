"""Read-only server audit of the last unfollow attempts; never sends unfollows."""
import os
import sys
from urllib.parse import quote

from friends_bot import BotError, Hevy, StateStore, own_username, usernames
from social_bot import attempts, statuses, valid_names


def audit(api, store, limit=10):
    own = own_username(api)
    history = attempts(store, "unfollow_attempts")
    names = [name for name, stamp in sorted(history.items(), key=lambda item: (item[1], item[0]))][-limit:]
    before = api.request("GET", "/follow_counts")
    rows = api.request("GET", "/following/" + quote(own, safe=""))
    following = valid_names(usernames(rows))
    after = api.request("GET", "/follow_counts")
    if (not isinstance(before, dict) or not isinstance(after, dict)
            or type(before.get("following_count")) is not int
            or before["following_count"] != after.get("following_count")
            or len(following) != before["following_count"] or len(rows) != len(following)):
        raise BotError("Liste actuelle des abonnements incomplete ou modifiee. Controle non conclu.")
    current_statuses = statuses(api)
    confirmed = sum(name not in following and current_statuses.get(name, "not-following") == "not-following"
                    for name in names)
    return len(names), confirmed, len(following)


def main():
    store = None
    try:
        store = StateStore(os.getenv("HEVY_AUTH_JSON", ""))
        checked, confirmed, total = audit(Hevy(store), store)
        text = (f"Audit desabonnements sur Hevy : {confirmed} absents confirmes sur {checked} comptes controles ; "
                f"liste complete de {total} abonnements verifiee. Aucun nouveau desabonnement envoye.")
        print(text)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
                summary.write(text + "\n")
        if confirmed != checked:
            raise BotError("Certains comptes controles sont toujours suivis ou ont ete suivis de nouveau.")
        return 0
    except BotError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print("Controle interrompu sans afficher de donnees privees.", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.save()


if __name__ == "__main__":
    sys.exit(main())

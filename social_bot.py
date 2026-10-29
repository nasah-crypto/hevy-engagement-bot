"""Follow back first, then discover; remove only observed lost followers."""
import argparse
import os
import re
import sys
import time
from urllib.parse import quote

from friends_bot import BotError, Hevy, StateStore, own_username, usernames

PER_RUN = 2
PER_24_HOURS = 20
UNFOLLOW_PER_RUN = 2
UNFOLLOW_PER_24_HOURS = 10
ABSENCE_SECONDS = 24 * 3600
USERNAME = re.compile(r"[A-Za-z0-9_.-]{1,100}")


def valid_names(values):
    if not all(isinstance(name, str) and USERNAME.fullmatch(name) for name in values):
        raise BotError("Identifiants sociaux inconnus. Traitement arrete.")
    return {name.casefold() for name in values}


def attempts(store, key):
    ledger = store.data.setdefault(key, {})
    if not isinstance(ledger, dict) or not all(
        isinstance(k, str) and USERNAME.fullmatch(k)
        and type(v) in (int, float) and 0 <= v < float("inf")
        for k, v in ledger.items()
    ):
        raise BotError("Historique social invalide. Traitement arrete.")
    return ledger


def followers(api, own):
    """Read every offset page and check its size against Hevy's two counts."""
    def count():
        counts = api.request("GET", "/follow_counts")
        if (not isinstance(counts, dict) or type(counts.get("follower_count")) is not int
                or not 0 <= counts["follower_count"] <= 10000):
            raise BotError("Nombre d'abonnes non reconnu. Aucun desabonnement envoye.")
        return counts["follower_count"]

    before = count()
    found = set()
    offset = 0
    for _ in range(501):
        data = api.request("GET", f"/followers_paged/{quote(own, safe='')}/{offset}")
        batch = valid_names(usernames(data))
        if not data:
            break
        if len(batch) != len(data) or found & batch:
            raise BotError("Pages d'abonnes incoherentes. Aucun desabonnement envoye.")
        found.update(batch)
        offset += len(data)
        if offset > 10000:
            raise BotError("Liste d'abonnes trop longue. Controle manuel necessaire.")
    else:
        raise BotError("Liste d'abonnes incomplete. Aucun desabonnement envoye.")
    if count() != before or len(found) != before:
        raise BotError("Liste d'abonnes incomplete ou en cours de modification. Aucun desabonnement envoye.")
    return found


def observe_followers(store, current, current_statuses, clock=time.time):
    """An initial non-mutual following is never evidence of a lost follower."""
    previous = store.data.get("followers_snapshot")
    pending = attempts(store, "lost_followers_since")
    first = previous is None
    if first:
        pending.clear()
    else:
        if not isinstance(previous, list):
            raise BotError("Historique des abonnes invalide. Aucun desabonnement envoye.")
        previous = valid_names(previous)
        lost = previous - current
        if len(lost) > max(10, len(previous) // 4):
            raise BotError("Baisse inhabituelle des abonnes. Desabonnements suspendus pour controle.")
        for name in lost:
            if current_statuses.get(name) == "following":
                pending.setdefault(name, clock())
        for name in list(pending):
            if name in current or current_statuses.get(name) != "following":
                del pending[name]
    store.data["followers_snapshot"] = sorted(current)
    store.data["followers_snapshot_at"] = clock()
    store.save()
    return first


def unfollow_lost(api, store, own, apply=False, clock=time.time):
    pending = attempts(store, "lost_followers_since")
    ledger = attempts(store, "unfollow_attempts")
    room = max(0, min(UNFOLLOW_PER_RUN, UNFOLLOW_PER_24_HOURS - sum(
        stamp >= clock() - 86400 for stamp in ledger.values())))
    eligible = [name for name, stamp in sorted(pending.items(), key=lambda item: (item[1], item[0]))
                if clock() - stamp >= ABSENCE_SECONDS and name not in ledger and name != own]
    confirmed = 0
    if apply:
        for name in eligible[:room]:
            if name in followers(api, own):
                pending.pop(name, None)
                store.save()
                continue
            if statuses(api).get(name) != "following":
                pending.pop(name, None)
                store.save()
                continue
            # Persist before the mutation: never repeat a possibly applied request.
            ledger[name] = clock()
            store.save()
            api.request("POST", "/unfollow", {"username": name})
            status = statuses(api).get(name, "not-following")
            if status != "not-following":
                time.sleep(2)
                status = statuses(api).get(name, "not-following")
            if status != "not-following":
                raise BotError("Desabonnement non confirme. Traitement arrete sans nouvelle tentative.")
            pending.pop(name, None)
            store.save()
            confirmed += 1
    return {"anciens_abonnes_absents_24h": len(eligible),
            "desabonnements_confirmes": confirmed}


def statuses(api):
    data = api.request("GET", "/following_statuses")
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and v in ("following", "requested", "not-following")
        for k, v in data.items()
    ):
        raise BotError("Format des abonnements inconnu. Aucun nouvel abonnement envoye.")
    return {k.casefold(): v for k, v in data.items()}


def follow_recommendations(api, store, apply=False, cap=PER_RUN, clock=time.time, priority=()):
    own = own_username(api)
    current = statuses(api)
    recommendations = api.request("GET", "/recommended_users")
    if not isinstance(recommendations, list) or not all(
        isinstance(user, dict) and isinstance(user.get("username"), str)
        and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", user["username"])
        for user in recommendations
    ):
        raise BotError("Format des recommandations inconnu. Aucun abonnement envoye.")
    ledger = attempts(store, "follow_attempts")
    removed = attempts(store, "unfollow_attempts")
    priority = valid_names(priority)
    # Count attempts, including an uncertain response, before sending requests.
    # This prevents timeouts or a rerun from silently multiplying follows.
    recent = sum(stamp >= clock() - 86400 for stamp in ledger.values())
    room = max(0, min(cap, PER_RUN, PER_24_HOURS - recent))
    ordered = sorted(priority) + [user["username"].casefold() for user in recommendations]
    eligible = list(dict.fromkeys(name for name in ordered
        if name != own and current.get(name, "not-following") == "not-following"
        and name not in ledger and name not in removed))
    confirmed = requested = returned = 0
    if apply and room and len(ledger) >= 10000:
        raise BotError("Historique des abonnements plein. Controle necessaire.")
    if apply:
        for username in eligible[:room]:
            if statuses(api).get(username, "not-following") != "not-following":
                continue
            ledger[username] = clock()
            store.save()
            api.request("POST", "/follow", {"username": username})
            status = statuses(api).get(username)
            if status not in ("following", "requested"):
                time.sleep(2)
                status = statuses(api).get(username)
            if status not in ("following", "requested"):
                raise BotError("Abonnement non confirme. Arret ; cette personne ne sera pas relancee automatiquement.")
            confirmed += status == "following"
            requested += status == "requested"
            returned += username in priority
    return {"candidats": len(eligible), "abonnements_confirmes": confirmed,
            "demandes_envoyees": requested, "dont_suivis_en_retour": returned,
            "retours_en_attente": sum(name in priority for name in eligible) - returned, "tentatives_24h": sum(
                stamp >= clock() - 86400 for stamp in ledger.values())}


def run_social(api, store, apply=False, unfollow=True, follow=True, clock=time.time):
    own = own_username(api)
    current = followers(api, own)
    first = observe_followers(store, current, statuses(api), clock)
    result = follow_recommendations(api, store, apply=apply and follow, clock=clock, priority=current)
    result.update(unfollow_lost(api, store, own, apply=apply and unfollow, clock=clock))
    result["abonnes_confirmes"] = len(current)
    result["premier_releve"] = first
    return result


def inspect_requests(api):
    """Read known endpoint only; do not guess any acceptance mutation."""
    account = api.request("GET", "/user/account")
    privacy = {key: value for key, value in account.items()
               if "private" in key.lower() and isinstance(value, bool)}
    data = api.request("GET", "/follow_requests")
    # Only structural metadata goes into public logs, never actual requests.
    if isinstance(data, list):
        result = {"nombre": len(data), "format": "liste"}
    elif isinstance(data, dict):
        result = {"format": "objet", "listes": {
            key: len(value) for key, value in data.items()
            if key in ("requests", "follow_requests", "incoming", "outgoing")
            and isinstance(value, list)}}
    else:
        raise BotError("Format des demandes inconnu. Aucune demande acceptee.")
    result["confidentialite"] = privacy
    result["acceptation_automatique"] = False
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--inspect-requests", action="store_true")
    parser.add_argument("--no-unfollow", action="store_true")
    parser.add_argument("--no-follow", action="store_true")
    args = parser.parse_args()
    store = None
    try:
        store = StateStore(os.getenv("HEVY_AUTH_JSON", ""))
        api = Hevy(store)
        result = run_social(api, store, apply=args.apply, unfollow=not args.no_unfollow,
                            follow=not args.no_follow)
        text = "Gestion sociale : " + str(result) + ". Abonnements : 2 par passage, 20 par 24 h ; desabonnements : 2 par passage, 10 par 24 h, apres 24 h d'absence confirmee."
        print(text)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
                summary.write(text + "\n")
        if args.inspect_requests:
            print("Demandes recues (lecture seule) : " + str(inspect_requests(api)))
        return 0
    except BotError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print("Erreur sociale inattendue. Arret sans afficher de donnees privees.", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.save()


if __name__ == "__main__":
    sys.exit(main())

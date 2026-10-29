"""Bounded follows from Hevy's own recommendations; no unfollows or messages."""
import argparse
import os
import re
import sys
import time

from friends_bot import BotError, Hevy, StateStore, own_username

PER_RUN = 2
PER_24_HOURS = 20


def statuses(api):
    data = api.request("GET", "/following_statuses")
    if not isinstance(data, dict) or not all(
        isinstance(k, str) and v in ("following", "requested", "not-following")
        for k, v in data.items()
    ):
        raise BotError("Format des abonnements inconnu. Aucun nouvel abonnement envoye.")
    return {k.casefold(): v for k, v in data.items()}


def follow_recommendations(api, store, apply=False, cap=PER_RUN, clock=time.time):
    own = own_username(api)
    current = statuses(api)
    recommendations = api.request("GET", "/recommended_users")
    if not isinstance(recommendations, list) or not all(
        isinstance(user, dict) and isinstance(user.get("username"), str)
        and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", user["username"])
        for user in recommendations
    ):
        raise BotError("Format des recommandations inconnu. Aucun abonnement envoye.")
    ledger = store.data.setdefault("follow_attempts", {})
    if not isinstance(ledger, dict) or not all(
        isinstance(k, str) and isinstance(v, (int, float)) for k, v in ledger.items()
    ):
        raise BotError("Historique des abonnements invalide. Traitement arrete.")
    # Count attempts, including an uncertain response, before sending requests.
    # This prevents timeouts or a rerun from silently multiplying follows.
    recent = sum(stamp >= clock() - 86400 for stamp in ledger.values())
    room = max(0, min(cap, PER_RUN, PER_24_HOURS - recent))
    eligible = list(dict.fromkeys(
        user["username"].casefold() for user in recommendations
        if user["username"].casefold() != own
        and current.get(user["username"].casefold(), "not-following") == "not-following"
        and user["username"].casefold() not in ledger
    ))
    confirmed = requested = 0
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
    return {"candidats": len(eligible), "abonnements_confirmes": confirmed,
            "demandes_envoyees": requested, "tentatives_24h": sum(
                stamp >= clock() - 86400 for stamp in ledger.values())}


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
    args = parser.parse_args()
    store = None
    try:
        store = StateStore(os.getenv("HEVY_AUTH_JSON", ""))
        api = Hevy(store)
        result = follow_recommendations(api, store, apply=args.apply)
        text = "Abonnements automatiques : " + str(result) + ". Plafonds : 2 par passage, 20 par 24 h."
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

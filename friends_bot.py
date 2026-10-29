"""Likes for existing Hevy friends. Check-only unless --apply is explicit.

Only encrypted session state is written to disk. No credentials or workout
details are printed to the public GitHub Actions log.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.fernet import Fernet, InvalidToken

BASE = "https://api.hevyapp.com"
STATE = Path(".hevy-state/session.enc")
PAGE_SIZE = 5
MAX_LIKES = 100


class BotError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_tokens(value):
    if not isinstance(value, dict) or not all(
        isinstance(value.get(k), str) and len(value[k]) >= 10
        for k in ("access_token", "refresh_token")
    ):
        raise BotError("Connexion absente ou invalide : renseigner HEVY_AUTH_JSON.")
    return {k: value[k] for k in ("access_token", "refresh_token")}


class StateStore:
    def __init__(self, seed, path=STATE):
        try:
            initial = validate_tokens(json.loads(seed))
        except (ValueError, TypeError):
            raise BotError("HEVY_AUTH_JSON doit contenir la connexion JSON.") from None
        canonical = json.dumps(initial, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(b"hevy-friends-encrypted-state-v1\0" + canonical).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(digest))
        self.cache_id = hashlib.sha256(b"cache-id\0" + digest).hexdigest()[:24]
        self.path = path
        self.data = {"tokens": initial, "liked": []}
        if path.exists():
            try:
                self.data = json.loads(self.cipher.decrypt(path.read_bytes()))
                validate_tokens(self.data["tokens"])
            except (InvalidToken, ValueError, KeyError, TypeError):
                raise BotError("Etat chiffre illisible. Refaire la connexion du bot.") from None

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(self.cipher.encrypt(json.dumps(self.data).encode()))
        tmp.chmod(0o600)
        tmp.replace(self.path)


class Hevy:
    def __init__(self, store):
        self.store = store
        self.opener = build_opener(NoRedirect())

    def raw(self, method, path, payload=None):
        headers = {
            "Authorization": "Bearer " + self.store.data["tokens"]["access_token"],
            "x-api-key": "shelobs_hevy_web",
            "hevy-platform": "web",
            "x-client-time": str(time.time()),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://hevy.com",
            "Referer": "https://hevy.com/",
        }
        body = None if payload is None else json.dumps(payload).encode()
        request = Request(BASE + path, data=body, headers=headers, method=method)
        time.sleep(1)
        try:
            with self.opener.open(request, timeout=25) as response:
                data = response.read()
                if not data:
                    return response.status, None
                try:
                    return response.status, json.loads(data)
                except ValueError:
                    return response.status, None
        except HTTPError as error:
            # Only known error codes can appear in logs; never response bodies.
            known = {"InvalidApiKey", "InvalidAuthToken", "MissingAuthToken",
                     "AccessTokenExpired", "AccessTokenInvalid", "MissingClientTime",
                     "InvalidClientTime", "InvalidRequest"}
            try:
                body = json.loads(error.read())
                code = body.get("error") if isinstance(body, dict) else None
                return error.code, {"error": code if isinstance(code, str) and code in known else None}
            except (ValueError, OSError):
                return error.code, None
        except (URLError, TimeoutError, OSError):
            raise BotError("Hevy injoignable. Reessayer plus tard.") from None

    def request(self, method, path, payload=None):
        status, data = self.raw(method, path, payload)
        if status == 401:
            status, refreshed = self.raw("POST", "/auth/refresh_token", {
                "refresh_token": self.store.data["tokens"]["refresh_token"]
            })
            if status != 200:
                raise BotError("Connexion Hevy expiree. Refaire la liaison depuis Safari.")
            self.store.data["tokens"] = validate_tokens(refreshed)
            # Save rotated credentials before any subsequent API call.
            self.store.save()
            status, data = self.raw(method, path, payload)
        if status == 429:
            raise BotError("Limite Hevy atteinte. Arret, sans nouvelles tentatives.")
        if status in (401, 403):
            raise BotError("Connexion refusee par Hevy. Refaire la liaison depuis Safari.")
        if not 200 <= status < 300:
            route = path.split("?")[0]
            if route not in ("/user/account", "/auth/refresh_token"):
                route = "/" + route.strip("/").split("/")[0]
            code = data.get("error") if isinstance(data, dict) else None
            detail = f" ({code})" if code else ""
            raise BotError(f"Hevy a renvoye HTTP {status} pour {method} {route}{detail}. Traitement arrete.")
        return data


def usernames(data):
    if not isinstance(data, list):
        raise BotError("Format de la liste d'amis inconnu. Aucun like envoye.")
    result = set()
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("username"), str):
            raise BotError("Format des amis inconnu. Aucun like envoye.")
        result.add(entry["username"].casefold())
    return result


def workout_date(workout):
    for key in ("created_at", "end_time", "start_time"):
        value = workout.get(key)
        if value is None:
            continue
        try:
            if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
                number = float(value)
                return number / 1000 if number > 100_000_000_000 else number
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (ValueError, TypeError, OverflowError):
            continue
    raise BotError("Date de seance inconnue. Arret avant de liker cette seance.")


def own_username(api):
    account = api.request("GET", "/user/account")
    if not isinstance(account, dict) or not isinstance(account.get("username"), str):
        raise BotError("Profil Hevy non reconnu. Aucun like envoye.")
    own = account["username"].casefold()
    expected = os.getenv("HEVY_EXPECTED_USERNAME", "").casefold()
    if expected and own != expected:
        raise BotError("Ce n'est pas le profil Hevy attendu. Aucun like envoye.")
    return own


def audit_likes(api, store, limit=50):
    own = own_username(api)
    ids = list(dict.fromkeys(store.data.get("liked", [])))[-limit:]
    confirmed = 0
    for wid in ids:
        if not isinstance(wid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", wid):
            raise BotError("Identifiant enregistre non reconnu. Controle arrete.")
        if own in usernames(api.request("GET", "/workout_likes/" + wid)):
            confirmed += 1
    return len(ids), confirmed


def run(api, store, apply=False, cap=MAX_LIKES, days=7, clock=time.time):
    own = own_username(api)
    friends = usernames(api.request("GET", "/following/" + quote(own, safe=""))) - {own}
    remembered = set(store.data.get("liked", []))
    cutoff = clock() - days * 86400
    # Collect across all followed accounts before selecting the newest posts.
    # Alphabetical traversal must not decide which friends receive the first likes.
    collected = {}
    for friend in sorted(friends):
        for page in range(40):
            params = urlencode({"username": friend, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE})
            data = api.request("GET", "/user_workouts_paged?" + params)
            if not isinstance(data, dict) or not isinstance(data.get("workouts"), list):
                raise BotError("Format des seances inconnu. Traitement arrete.")
            workouts = data["workouts"]
            old = 0
            for workout in workouts:
                if not isinstance(workout, dict):
                    raise BotError("Format de seance inconnu.")
                created = workout_date(workout)
                if created < cutoff:
                    old += 1
                    continue
                wid = str(workout.get("id", ""))
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", wid):
                    raise BotError("Identifiant de seance inconnu.")
                if wid in remembered:
                    continue
                collected.setdefault(wid, (created, friend))
            if len(workouts) < PAGE_SIZE or (workouts and old == len(workouts)):
                break
    candidates = sent = 0
    ordered = sorted(collected.items(), key=lambda item: (-item[1][0], item[0]))
    for wid, (_, friend) in ordered:
        likers = usernames(api.request("GET", "/workout_likes/" + wid))
        if own in likers:
            continue
        candidates += 1
        if apply:
            current = usernames(api.request("GET", "/following/" + quote(own, safe="")))
            if friend not in current or friend == own:
                continue
            api.request("POST", "/workout/like/" + wid)
            verified = usernames(api.request("GET", "/workout_likes/" + wid))
            if own not in verified:
                # Give the server one short opportunity to make the update visible.
                time.sleep(2)
                verified = usernames(api.request("GET", "/workout_likes/" + wid))
            if own not in verified:
                raise BotError("Hevy n'a pas confirme le like apres envoi. Traitement arrete.")
            remembered.add(wid)
            store.data["liked"] = sorted(remembered)[-10000:]
            store.save()
            sent += 1
        if (sent if apply else candidates) >= cap:
            return len(friends), candidates, sent, True
    return len(friends), candidates, sent, False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--cache-id", action="store_true")
    args = parser.parse_args()
    store = None
    try:
        store = StateStore(os.getenv("HEVY_AUTH_JSON", ""))
        if args.cache_id:
            print(store.cache_id)
            return 0
        if args.audit:
            checked, confirmed = audit_likes(Hevy(store), store)
            print(f"Controle sur Hevy : {confirmed} likes confirmes sur {checked} likes enregistres. Aucun nouveau like envoye.")
            if confirmed != checked:
                raise BotError("Certains likes enregistres ne sont pas presents sur Hevy.")
            return 0
        friends, found, sent, capped = run(Hevy(store), store, apply=args.apply)
        text = (f"Amis suivis : {friends}. Seances a liker : {found}. "
                f"Likes envoyes : {sent}. Mode : {'likes' if args.apply else 'verification'}. "
                "Periode : 7 derniers jours.")
        if capped:
            text += f" Limite de {MAX_LIKES} atteinte ; les autres seront examines au prochain passage."
        print(text)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
                summary.write(text + "\n")
        return 0
    except BotError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        # Do not leak tokens via exception details or request dumps.
        print("Erreur inattendue. Traitement arrete, sans afficher de donnees privees.", file=sys.stderr)
        return 1
    finally:
        if store is not None and not args.cache_id:
            store.save()


if __name__ == "__main__":
    sys.exit(main())

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from friends_bot import BotError, Hevy, StateStore, audit_likes, run, workout_date
from urllib.parse import parse_qs, urlsplit


SEED = json.dumps({"access_token": "fake-access-token-1234", "refresh_token": "fake-refresh-token-5678"})
NOW = 1_800_000_000


class FakeAPI:
    def __init__(self, following=None, liked=None, unfollow_before_write=False, workouts=None, confirm_writes=True):
        self.calls = []
        self.following = following if following is not None else [{"username": "friend"}, {"username": "me"}]
        self.liked = liked or []
        self.unfollow_before_write = unfollow_before_write
        self.following_reads = 0
        self.workouts = workouts
        self.confirm_writes = confirm_writes
        self.written = set()

    def request(self, method, path):
        self.calls.append((method, path))
        if path == "/user/account":
            return {"username": "me"}
        if path == "/following/me":
            self.following_reads += 1
            return [] if self.unfollow_before_write and self.following_reads > 1 else self.following
        if path.startswith("/user_workouts_paged?"):
            if "username=me&" in path:
                raise AssertionError("Own workouts must never be selected")
            if self.workouts is not None:
                params = parse_qs(urlsplit(path).query)
                start = int(params["offset"][0])
                limit = int(params["limit"][0])
                return {"workouts": self.workouts.get(params["username"][0], [])[start:start + limit]}
            return {"workouts": [
                {"id": "new-1", "created_at": NOW - 60},
                {"id": "old-1", "created_at": NOW - 8 * 86400},
            ]}
        if path.startswith("/workout_likes/"):
            wid = path.rsplit("/", 1)[1]
            return ([{"username": "me"}] if wid in self.written else
                    self.liked if wid == "new-1" else [])
        if method == "POST" and path.startswith("/workout/like/"):
            if self.confirm_writes:
                self.written.add(path.rsplit("/", 1)[1])
            return None
        raise AssertionError((method, path))


class FriendsTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "session.enc"
        self.store = StateStore(SEED, self.path)
        self.env = patch.dict("os.environ", {"HEVY_EXPECTED_USERNAME": ""})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_check_mode_never_sends_likes(self):
        api = FakeAPI()
        self.assertEqual(run(api, self.store, clock=lambda: NOW), (1, 1, 0, False))
        self.assertFalse(any(method == "POST" for method, _ in api.calls))

    def test_only_followed_friend_recent_workouts_and_no_repeat(self):
        api = FakeAPI()
        self.assertEqual(run(api, self.store, apply=True, clock=lambda: NOW), (1, 1, 1, False))
        self.assertEqual([path for method, path in api.calls if method == "POST"], ["/workout/like/new-1"])
        again = FakeAPI()
        self.assertEqual(run(again, self.store, apply=True, clock=lambda: NOW)[2], 0)
        self.assertFalse(any(method == "POST" for method, _ in again.calls))

    def test_previously_liked_on_hevy_is_skipped(self):
        api = FakeAPI(liked=[{"username": "ME"}])
        self.assertEqual(run(api, self.store, apply=True, clock=lambda: NOW)[2], 0)
        self.assertFalse(any(method == "POST" for method, _ in api.calls))

    def test_unfollowed_during_scan_is_not_liked(self):
        api = FakeAPI(unfollow_before_write=True)
        self.assertEqual(run(api, self.store, apply=True, clock=lambda: NOW)[2], 0)

    def test_unrecognized_following_fails_before_any_write(self):
        api = FakeAPI(following={"unexpected": []})
        with self.assertRaises(BotError):
            run(api, self.store, apply=True, clock=lambda: NOW)
        self.assertFalse(any(method == "POST" for method, _ in api.calls))

    def test_wrong_account_is_not_used(self):
        with patch.dict("os.environ", {"HEVY_EXPECTED_USERNAME": "different"}):
            with self.assertRaises(BotError):
                run(FakeAPI(), self.store, apply=True, clock=lambda: NOW)

    def test_cap_stops_writes(self):
        self.assertEqual(run(FakeAPI(), self.store, apply=True, cap=1, clock=lambda: NOW), (1, 1, 1, True))

    def test_newest_workout_across_friends_wins_over_alphabetical_order(self):
        api = FakeAPI(following=[{"username": "alice"}, {"username": "zoe"}], workouts={
            "alice": [{"id": "alice-older", "created_at": NOW - 300}],
            "zoe": [{"id": "zoe-newest", "created_at": NOW - 10}],
        })
        self.assertEqual(run(api, self.store, apply=True, cap=1, clock=lambda: NOW)[2], 1)
        self.assertEqual([p for m, p in api.calls if m == "POST"], ["/workout/like/zoe-newest"])

    def test_sort_is_global_and_handles_more_than_one_page(self):
        posts = [{"id": f"w-{i}", "created_at": NOW - 100 + i} for i in range(6)]
        api = FakeAPI(workouts={"friend": posts})
        run(api, self.store, apply=True, cap=2, clock=lambda: NOW)
        self.assertTrue(any("offset=5" in p for _, p in api.calls))
        self.assertEqual([p for m, p in api.calls if m == "POST"], ["/workout/like/w-5", "/workout/like/w-4"])

    def test_unconfirmed_write_is_not_counted_or_cached(self):
        api = FakeAPI(confirm_writes=False)
        with patch("friends_bot.time.sleep"):
            with self.assertRaises(BotError):
                run(api, self.store, apply=True, clock=lambda: NOW)
        self.assertEqual(self.store.data["liked"], [])

    def test_audit_reads_server_and_detects_missing_likes_without_writes(self):
        self.store.data["liked"] = ["new-1", "missing-1"]
        api = FakeAPI(liked=[{"username": "ME"}])
        self.assertEqual(audit_likes(api, self.store), (2, 1))
        self.assertFalse(any(method == "POST" for method, _ in api.calls))

    def test_state_is_encrypted_and_roundtrips(self):
        self.store.data["tokens"]["refresh_token"] = "rotated-fake-refresh-9999"
        self.store.save()
        saved = self.path.read_bytes()
        self.assertNotIn(b"fake", saved)
        self.assertNotIn(b"refresh_token", saved)
        self.assertEqual(StateStore(SEED, self.path).data, self.store.data)

    def test_refresh_saved_and_request_retried(self):
        api = Hevy(self.store)
        refreshed = {"access_token": "rotated-access-token-123", "refresh_token": "rotated-refresh-token-456"}
        with patch.object(api, "raw", side_effect=[(401, None), (200, refreshed), (200, {"username": "me"})]) as raw:
            self.assertEqual(api.request("GET", "/user/account"), {"username": "me"})
            self.assertEqual(raw.call_args_list[1].args[:2], ("POST", "/auth/refresh_token"))
        self.assertEqual(StateStore(SEED, self.path).data["tokens"], refreshed)

    def test_rate_limit_stops_without_retry(self):
        api = Hevy(self.store)
        with patch.object(api, "raw", return_value=(429, None)) as raw:
            with self.assertRaises(BotError):
                api.request("POST", "/workout/like/new-1")
            raw.assert_called_once()

    def test_unknown_date_fails(self):
        with self.assertRaises(BotError):
            workout_date({"id": "new-1"})
        self.assertEqual(workout_date({"created_at": "2026-10-29T12:00:00Z"}), 1793275200)


if __name__ == "__main__":
    unittest.main()

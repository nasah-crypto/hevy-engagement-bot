import unittest
from unittest.mock import patch

import test_friends_bot as fixtures
from friends_bot import BotError
from social_bot import follow_recommendations, inspect_requests


class SocialAPI:
    def __init__(self, state=None, confirm=True, fail=False):
        self.state = state or {"existing": "following", "waiting": "requested"}
        self.confirm = confirm
        self.fail = fail
        self.posts = []

    def request(self, method, path, payload=None):
        if path == "/user/account":
            return {"username": "me", "is_private": False}
        if path == "/following_statuses":
            return self.state.copy()
        if path == "/recommended_users":
            return [{"username": name} for name in
                    ("me", "existing", "waiting", "new1", "new1", "new2", "new3")]
        if path == "/follow_requests":
            return [{"username": "private-person"}]
        if method == "POST" and path == "/follow":
            self.posts.append(payload["username"])
            if self.fail:
                raise BotError("Simulated uncertain write")
            if self.confirm:
                self.state[payload["username"]] = "following"
            return None
        raise AssertionError((method, path))


NOW = fixtures.NOW


class SocialTests(unittest.TestCase):
    setUp = fixtures.FriendsTests.setUp
    def test_social_check_never_follows(self):
        api = SocialAPI()
        self.assertEqual(follow_recommendations(api, self.store)["candidats"], 3)
        self.assertEqual(api.posts, [])

    def test_social_only_new_accounts_two_per_run_no_duplicates(self):
        api = SocialAPI()
        result = follow_recommendations(api, self.store, apply=True, clock=lambda: NOW)
        self.assertEqual(result["abonnements_confirmes"], 2)
        self.assertEqual(api.posts, ["new1", "new2"])
        follow_recommendations(api, self.store, apply=True, clock=lambda: NOW)
        self.assertEqual(api.posts, ["new1", "new2", "new3"])

    def test_rolling_daily_cap_survives_reruns_and_midnight(self):
        self.store.data["follow_attempts"] = {f"prior{i}": NOW - 100 for i in range(19)}
        api = SocialAPI()
        follow_recommendations(api, self.store, apply=True, clock=lambda: NOW)
        follow_recommendations(api, self.store, apply=True, clock=lambda: NOW + 3600)
        self.assertEqual(api.posts, ["new1"])

    def test_uncertain_follow_is_recorded_before_network_call_and_not_retried(self):
        api = SocialAPI(fail=True)
        with self.assertRaises(BotError):
            follow_recommendations(api, self.store, apply=True, clock=lambda: NOW)
        self.assertIn("new1", self.store.data["follow_attempts"])
        api.fail = False
        follow_recommendations(api, self.store, apply=True, clock=lambda: NOW)
        self.assertEqual(api.posts.count("new1"), 1)

    def test_unconfirmed_follow_is_not_reported_success(self):
        with patch("social_bot.time.sleep"):
            with self.assertRaises(BotError):
                follow_recommendations(SocialAPI(confirm=False), self.store, apply=True)

    def test_request_inspection_is_private_and_does_not_accept(self):
        api = SocialAPI()
        result = inspect_requests(api)
        self.assertEqual(result["nombre"], 1)
        self.assertNotIn("private-person", str(result))
        self.assertEqual(api.posts, [])
        self.assertFalse(result["acceptation_automatique"])


if __name__ == "__main__":
    unittest.main()

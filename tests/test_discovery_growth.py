import unittest
import test_friends_bot as fixtures
from test_social_bot import SocialAPI
from friends_bot import BotError
from social_bot import follow_recommendations


class NetworkAPI(SocialAPI):
    def __init__(self):
        super().__init__()
        self.sources = []
        self.rows = [{"username": f"fresh{i}"} for i in range(6)]

    def request(self, method, path, payload=None):
        if path == "/recommended_users":
            return []
        if path.startswith("/following/"):
            self.sources.append(path)
            return self.rows
        return super().request(method, path, payload)


class GrowthTests(unittest.TestCase):
    setUp = fixtures.FriendsTests.setUp

    def test_exhausted_recommendations_find_four_fresh_accounts(self):
        api = NetworkAPI()
        result = follow_recommendations(api, self.store, apply=True, expand_discovery=True)
        self.assertEqual(api.posts, ["fresh0", "fresh1", "fresh2", "fresh3"])
        self.assertEqual(result["abonnements_confirmes"], 4)
        self.assertEqual(len(api.sources), 1)

    def test_preview_reads_candidates_without_following(self):
        api = NetworkAPI()
        self.assertEqual(follow_recommendations(api, self.store, expand_discovery=True)["candidats"], 6)
        self.assertEqual(api.posts, [])

    def test_followbacks_keep_priority_and_do_not_need_extra_source(self):
        api = NetworkAPI()
        names = {f"return{i}" for i in range(5)}
        follow_recommendations(api, self.store, apply=True, priority=names, expand_discovery=True)
        self.assertEqual(api.posts, [f"return{i}" for i in range(4)])
        self.assertEqual(api.sources, [])

    def test_daily_cap_is_40_even_with_more_sources(self):
        api = NetworkAPI()
        self.store.data["follow_attempts"] = {f"prior{i}": fixtures.NOW for i in range(39)}
        follow_recommendations(api, self.store, apply=True, clock=lambda: fixtures.NOW, expand_discovery=True)
        follow_recommendations(api, self.store, apply=True, clock=lambda: fixtures.NOW, expand_discovery=True)
        self.assertEqual(api.posts, ["fresh0"])

    def test_source_rotates_and_malformed_data_stops_before_posts(self):
        api = NetworkAPI()
        api.state["other-source"] = "following"
        follow_recommendations(api, self.store, expand_discovery=True)
        follow_recommendations(api, self.store, expand_discovery=True)
        self.assertEqual(api.sources, ["/following/existing", "/following/other-source"])
        api.rows = {"unexpected": []}
        with self.assertRaises(BotError):
            follow_recommendations(api, self.store, apply=True, expand_discovery=True)
        self.assertEqual(api.posts, [])

    def test_old_attempts_and_removed_users_are_not_followed_again(self):
        api = NetworkAPI()
        self.store.data["follow_attempts"] = {"fresh0": fixtures.NOW}
        self.store.data["unfollow_attempts"] = {"fresh1": fixtures.NOW}
        follow_recommendations(api, self.store, apply=True, expand_discovery=True)
        self.assertEqual(api.posts, ["fresh2", "fresh3", "fresh4", "fresh5"])

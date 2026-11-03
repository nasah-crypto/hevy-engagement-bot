import unittest
import test_friends_bot as fixtures
from test_social_balance import BalanceAPI
from friends_bot import BotError
from audit_unfollows import audit


class UnfollowAuditTests(unittest.TestCase):
    setUp = fixtures.FriendsTests.setUp

    def test_reads_actual_following_list_without_mutations(self):
        api = BalanceAPI(state={"still-followed": "following"})
        self.store.data["unfollow_attempts"] = {"removed": fixtures.NOW}
        self.assertEqual(audit(api, self.store), (1, 1, 1))
        self.assertEqual(api.posts + api.unfollows, [])

    def test_detects_account_that_is_still_followed(self):
        api = BalanceAPI(state={"still-followed": "following"})
        self.store.data["unfollow_attempts"] = {"still-followed": fixtures.NOW}
        self.assertEqual(audit(api, self.store), (1, 0, 1))

    def test_incomplete_list_cannot_confirm_absence(self):
        api = BalanceAPI(state={"still-followed": "following"})
        self.store.data["unfollow_attempts"] = {"removed": fixtures.NOW}
        original = api.request
        api.request = lambda method, path, payload=None: [] if path == "/following/me" else original(method, path, payload)
        with self.assertRaises(BotError):
            audit(api, self.store)

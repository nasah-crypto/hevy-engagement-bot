import unittest
import test_friends_bot as fixtures
from test_social_balance import BalanceAPI
from social_bot import run_social, matured_nonmutuals, FOLLOWBACK_WAIT_SECONDS, unfollow_lost

NOW = fixtures.NOW


class MatureCleanupTests(unittest.TestCase):
    setUp = fixtures.FriendsTests.setUp

    def test_old_bot_follow_is_removed_but_recent_mutual_and_requested_are_kept(self):
        api = BalanceAPI(fans={"mutual"}, state={"old": "following", "recent": "following",
            "mutual": "following", "waiting": "requested"})
        self.store.data["follow_attempts"] = {name: NOW - FOLLOWBACK_WAIT_SECONDS for name in api.state}
        self.store.data["follow_attempts"]["recent"] = NOW - 60
        result = run_social(api, self.store, apply=True, follow=False, clock=lambda: NOW)
        self.assertEqual(api.unfollows, ["old"])
        self.assertEqual(result["non_reciproques_apres_72h_retires"], 1)

    def test_72_hour_boundary(self):
        self.store.data["follow_attempts"] = {"old": NOW - FOLLOWBACK_WAIT_SECONDS + 1}
        self.assertEqual(matured_nonmutuals(self.store, set(), {"old": "following"}, lambda: NOW), [])
        self.assertEqual(matured_nonmutuals(self.store, set(), {"old": "following"}, lambda: NOW + 1), ["old"])

    def test_recently_lost_follower_keeps_24_hour_grace(self):
        self.store.data["follow_attempts"] = {"old": NOW - 5 * 86400}
        self.store.data["lost_followers_since"] = {"old": NOW - 60}
        self.assertEqual(matured_nonmutuals(self.store, set(), {"old": "following"}, lambda: NOW), [])

    def test_person_who_follows_before_write_is_not_removed(self):
        api = BalanceAPI(fans={"old"}, state={"old": "following"})
        unfollow_lost(api, self.store, "me", apply=True, mature_nonmutual=["old"], clock=lambda: NOW)
        self.assertEqual(api.unfollows, [])

    def test_shared_daily_cap_includes_all_cleanup_sources(self):
        api = BalanceAPI(state={"old1": "following", "old2": "following"})
        self.store.data["unfollow_attempts"] = {f"prior{i}": NOW - 1 for i in range(49)}
        result = unfollow_lost(api, self.store, "me", apply=True,
                              mature_nonmutual=["old1", "old2"], clock=lambda: NOW)
        self.assertEqual(api.unfollows, ["old1"])
        self.assertEqual(result["desabonnements_confirmes"], 1)

    def test_preview_does_not_remove_mature_nonmutuals(self):
        api = BalanceAPI(state={"old": "following"})
        self.store.data["follow_attempts"] = {"old": NOW - 5 * 86400}
        run_social(api, self.store, clock=lambda: NOW)
        self.assertEqual(api.unfollows, [])

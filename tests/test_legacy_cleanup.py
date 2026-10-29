import unittest
from unittest.mock import patch

import test_friends_bot as fixtures
from test_social_balance import BalanceAPI
from friends_bot import BotError
from social_bot import run_social, unfollow_lost

NOW = fixtures.NOW


class LegacyCleanupTests(unittest.TestCase):
    setUp = fixtures.FriendsTests.setUp

    def test_removes_old_nonmutuals_but_preserves_mutual_recent_and_pending(self):
        api = BalanceAPI(fans={"mutual"}, state={"mutual": "following", "old1": "following",
            "old2": "following", "new1": "following", "pending": "requested"})
        self.store.data["follow_attempts"] = {"new1": NOW - 100}
        result = run_social(api, self.store, apply=True, follow=False,
                            cleanup_existing=True, clock=lambda: NOW)
        self.assertEqual(api.unfollows, ["old1", "old2"])
        self.assertEqual(result["anciens_non_reciproques_retires"], 2)
        self.assertEqual(api.state["mutual"], "following")
        self.assertEqual(api.state["new1"], "following")
        self.assertEqual(api.state["pending"], "requested")

    def test_check_mode_freezes_old_backlog_without_unfollowing(self):
        api = BalanceAPI(fans={"fan"})
        result = run_social(api, self.store, cleanup_existing=True, clock=lambda: NOW)
        self.assertEqual(result["anciens_non_reciproques_restants"], 1)
        self.assertEqual(api.posts + api.unfollows, [])
        self.assertEqual(self.store.data["existing_nonmutual_queue"], ["existing"])

    def test_new_nonmutuals_are_never_added_to_an_existing_backlog(self):
        api = BalanceAPI(fans={"fan"})
        run_social(api, self.store, cleanup_existing=True, clock=lambda: NOW)
        api.state["recent-manual"] = "following"
        run_social(api, self.store, cleanup_existing=True, clock=lambda: NOW + 100)
        self.assertEqual(self.store.data["existing_nonmutual_queue"], ["existing"])

    def test_person_who_starts_following_us_before_write_is_preserved(self):
        api = BalanceAPI(fans={"fan"})
        run_social(api, self.store, cleanup_existing=True, clock=lambda: NOW)
        api.fans.add("existing")
        unfollow_lost(api, self.store, "me", apply=True, cleanup_existing=True, clock=lambda: NOW)
        self.assertEqual(api.unfollows, [])
        self.assertEqual(self.store.data["existing_nonmutual_queue"], [])

    def test_incomplete_following_list_stops_before_all_mutations(self):
        api = BalanceAPI(fans={"fan"})
        original = api.request
        def incomplete(method, path, payload=None):
            return [] if path == "/following/me" else original(method, path, payload)
        with patch.object(api, "request", side_effect=incomplete):
            with self.assertRaises(BotError):
                run_social(api, self.store, apply=True, cleanup_existing=True, clock=lambda: NOW)
        self.assertEqual(api.posts + api.unfollows, [])
        self.assertNotIn("existing_nonmutual_queue", self.store.data)

    def test_old_cleanup_and_future_losses_share_the_daily_unfollow_cap(self):
        api = BalanceAPI(fans={"fan"}, state={"old1": "following", "old2": "following"})
        self.store.data["unfollow_attempts"] = {f"prior{i}": NOW - 100 for i in range(9)}
        run_social(api, self.store, apply=True, follow=False, cleanup_existing=True, clock=lambda: NOW)
        run_social(api, self.store, apply=True, follow=False, cleanup_existing=True, clock=lambda: NOW + 100)
        self.assertEqual(api.unfollows, ["old1"])

    def test_completed_backlog_is_not_recreated_with_later_new_contacts(self):
        api = BalanceAPI(fans={"fan"})
        run_social(api, self.store, apply=True, follow=False, cleanup_existing=True, clock=lambda: NOW)
        api.state["recent-manual"] = "following"
        run_social(api, self.store, apply=True, follow=False, cleanup_existing=True, clock=lambda: NOW + 100)
        self.assertEqual(api.unfollows, ["existing"])
        self.assertEqual(api.state["recent-manual"], "following")

    def test_uncertain_old_cleanup_is_not_repeated(self):
        api = BalanceAPI(fans={"fan"}, confirm=False)
        with patch("social_bot.time.sleep"):
            with self.assertRaises(BotError):
                run_social(api, self.store, apply=True, follow=False, cleanup_existing=True, clock=lambda: NOW)
        run_social(api, self.store, apply=True, follow=False, cleanup_existing=True, clock=lambda: NOW + 100)
        self.assertEqual(api.unfollows, ["existing"])

    def test_unfollow_pause_also_suspends_old_cleanup(self):
        api = BalanceAPI(fans={"fan"})
        run_social(api, self.store, apply=True, follow=False, unfollow=False,
                    cleanup_existing=True, clock=lambda: NOW)
        self.assertEqual(api.unfollows, [])
        self.assertEqual(self.store.data["existing_nonmutual_queue"], ["existing"])


if __name__ == "__main__":
    unittest.main()

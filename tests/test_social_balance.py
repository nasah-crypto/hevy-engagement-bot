import unittest
from unittest.mock import patch

import test_friends_bot as fixtures
from test_social_bot import SocialAPI
from friends_bot import BotError, StateStore
from social_bot import (ABSENCE_SECONDS, followers, follow_recommendations,
                        observe_followers, run_social, statuses, unfollow_lost)

NOW = fixtures.NOW


class BalanceAPI(SocialAPI):
    def __init__(self, fans=(), state=None, count=None, confirm=True):
        super().__init__(state=state, confirm=confirm)
        self.fans = set(fans)
        self.count = count
        self.unfollows = []
        self.duplicate = False

    def request(self, method, path, payload=None):
        if path == "/user/account":
            return {"username": "me"}
        if path == "/follow_counts":
            return {"follower_count": len(self.fans) if self.count is None else self.count,
                    "following_count": sum(v == "following" for v in self.state.values())}
        if path == "/following/me":
            return [{"username": n} for n, status in self.state.items() if status == "following"]
        if path.startswith("/followers_paged/me/"):
            offset = int(path.rsplit("/", 1)[1])
            offset = 0 if self.duplicate else offset
            return [{"username": name} for name in sorted(self.fans)[offset:offset + 2]]
        if method == "POST" and path == "/unfollow":
            self.unfollows.append(payload["username"])
            if self.confirm:
                self.state.pop(payload["username"], None)
            return None
        return super().request(method, path, payload)


class BalanceTests(unittest.TestCase):
    setUp = fixtures.FriendsTests.setUp

    def record_loss(self, api, clock=NOW):
        observe_followers(self.store, set(api.fans) | {"existing"}, statuses(api), lambda: clock - 100)
        observe_followers(self.store, set(api.fans), statuses(api), lambda: clock)

    def test_complete_followers_uses_offsets_across_all_pages(self):
        api = BalanceAPI(fans={"alice", "bob", "charlie", "dave", "eve"})
        self.assertEqual(followers(api, "me"), api.fans)

    def test_first_snapshot_does_not_remove_existing_nonreciprocal_follows(self):
        api = BalanceAPI(fans={"fan"})
        result = run_social(api, self.store, apply=True, clock=lambda: NOW)
        self.assertTrue(result["premier_releve"])
        self.assertEqual(api.unfollows, [])
        self.assertEqual(self.store.data["lost_followers_since"], {})

    def test_followbacks_precede_recommendations_and_share_the_same_cap(self):
        api = BalanceAPI(fans={"zoe", "zara", "zack"})
        result = run_social(api, self.store, apply=True, clock=lambda: NOW)
        self.assertEqual(api.posts, ["zack", "zara"])
        self.assertEqual(result["dont_suivis_en_retour"], 2)
        self.assertEqual(result["retours_en_attente"], 1)

    def test_shared_daily_cap_applies_to_followbacks_too(self):
        self.store.data["follow_attempts"] = {f"prior{i}": NOW - 10 for i in range(19)}
        api = BalanceAPI(fans={"zoe", "zara"})
        run_social(api, self.store, apply=True, clock=lambda: NOW)
        self.assertEqual(api.posts, ["zara"])

    def test_check_mode_observes_but_sends_no_follow_or_unfollow(self):
        api = BalanceAPI(fans={"fan"})
        self.record_loss(api)
        run_social(api, self.store, clock=lambda: NOW + ABSENCE_SECONDS)
        self.assertEqual(api.posts, [])
        self.assertEqual(api.unfollows, [])

    def test_lost_follower_waits_24_hours_and_is_verified_before_unfollowing(self):
        api = BalanceAPI(fans={"fan"})
        self.record_loss(api)
        unfollow_lost(api, self.store, "me", apply=True, clock=lambda: NOW + ABSENCE_SECONDS - 1)
        self.assertEqual(api.unfollows, [])
        result = unfollow_lost(api, self.store, "me", apply=True, clock=lambda: NOW + ABSENCE_SECONDS)
        self.assertEqual(api.unfollows, ["existing"])
        self.assertEqual(result["desabonnements_confirmes"], 1)

    def test_returning_follower_is_rechecked_immediately_and_not_removed(self):
        api = BalanceAPI(fans={"fan"})
        self.record_loss(api)
        api.fans.add("existing")
        unfollow_lost(api, self.store, "me", apply=True, clock=lambda: NOW + ABSENCE_SECONDS)
        self.assertEqual(api.unfollows, [])

    def test_pending_outgoing_request_is_not_unfollowed(self):
        api = BalanceAPI(fans={"fan"})
        self.record_loss(api)
        api.state["existing"] = "requested"
        unfollow_lost(api, self.store, "me", apply=True, clock=lambda: NOW + ABSENCE_SECONDS)
        self.assertEqual(api.unfollows, [])

    def test_incomplete_follower_list_stops_before_all_writes(self):
        api = BalanceAPI(fans={"fan"}, count=100)
        with self.assertRaises(BotError):
            run_social(api, self.store, apply=True, clock=lambda: NOW)
        self.assertEqual(api.posts + api.unfollows, [])

    def test_unrecognized_count_contract_blocks_all_writes(self):
        api = BalanceAPI(fans={"fan"}, count="1")
        with self.assertRaises(BotError):
            run_social(api, self.store, apply=True, clock=lambda: NOW)
        self.assertEqual(api.posts + api.unfollows, [])

    def test_duplicate_or_repeated_pages_stop_without_establishing_baseline(self):
        api = BalanceAPI(fans={"a", "b", "c"})
        api.duplicate = True
        with self.assertRaises(BotError):
            run_social(api, self.store, apply=True, clock=lambda: NOW)
        self.assertNotIn("followers_snapshot", self.store.data)
        self.assertEqual(api.posts + api.unfollows, [])

    def test_large_follower_drop_stops_before_mutations(self):
        original = {f"fan{i}" for i in range(20)}
        observe_followers(self.store, original, {}, lambda: NOW)
        with self.assertRaises(BotError):
            observe_followers(self.store, set(), {}, lambda: NOW + 100)
        self.assertEqual(set(self.store.data["followers_snapshot"]), original)

    def test_unconfirmed_unfollow_is_persisted_but_never_retried(self):
        api = BalanceAPI(fans={"fan"}, confirm=False)
        self.record_loss(api)
        with patch("social_bot.time.sleep"):
            with self.assertRaises(BotError):
                unfollow_lost(api, self.store, "me", apply=True, clock=lambda: NOW + ABSENCE_SECONDS)
        self.assertIn("existing", StateStore(fixtures.SEED, self.path).data["unfollow_attempts"])
        unfollow_lost(api, self.store, "me", apply=True, clock=lambda: NOW + ABSENCE_SECONDS + 1)
        self.assertEqual(api.unfollows, ["existing"])

    def test_unfollow_daily_and_per_run_caps(self):
        api = BalanceAPI()
        for name in ("lost1", "lost2", "lost3"):
            api.state[name] = "following"
        self.store.data["lost_followers_since"] = {name: NOW - ABSENCE_SECONDS for name in ("lost1", "lost2", "lost3")}
        unfollow_lost(api, self.store, "me", apply=True, clock=lambda: NOW)
        self.assertEqual(len(api.unfollows), 2)
        self.store.data["unfollow_attempts"].update({f"prior{i}": NOW - 10 for i in range(8)})
        unfollow_lost(api, self.store, "me", apply=True, clock=lambda: NOW + 100)
        self.assertEqual(len(api.unfollows), 2)

    def test_bot_never_refollows_an_account_it_removed(self):
        self.store.data["unfollow_attempts"] = {"new1": NOW - 100}
        api = BalanceAPI()
        follow_recommendations(api, self.store, apply=True, clock=lambda: NOW, priority={"new1"})
        self.assertNotIn("new1", api.posts)

    def test_pause_following_still_allows_authorized_lost_follower_removal(self):
        api = BalanceAPI(fans={"fan"})
        self.record_loss(api)
        run_social(api, self.store, apply=True, follow=False, clock=lambda: NOW + ABSENCE_SECONDS)
        self.assertEqual(api.posts, [])
        self.assertEqual(api.unfollows, ["existing"])

    def test_pause_unfollowing_still_allows_followbacks(self):
        api = BalanceAPI(fans={"fan"})
        self.record_loss(api)
        run_social(api, self.store, apply=True, unfollow=False, clock=lambda: NOW + ABSENCE_SECONDS)
        self.assertEqual(api.unfollows, [])
        self.assertIn("fan", api.posts)


if __name__ == "__main__":
    unittest.main()

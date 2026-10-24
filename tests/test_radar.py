import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from radar.analysis import comparison, enrich, classify, meta_summary
from radar.config import DEFAULTS, validate_settings
from radar.db import Database
from radar.sources import SteamRows, SteamSource, RobloxSource, parse_release, resolve_game


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.meta = {"platform": "roblox", "app_id": "1", "name": "Survive the Night", "date_kind": "project_created", "release_date": "2026-01-01", "upcoming": False, "feeds": []}
        self.row = {"key": "roblox:1", "metadata": self.meta, "first_seen": self.now-86400, "last_seen": self.now, "watched": 0, "note": "", "release_override": None}

    def test_no_invented_growth_or_zero_base_percent(self):
        self.assertIsNone(comparison([{"ts": self.now, "ccu": 100}], "ccu", 86400))
        points = [{"ts": self.now-86400, "ccu": 0}, {"ts": self.now, "ccu": 100}]
        self.assertIsNone(comparison(points, "ccu", 86400)["pct"])

    def test_growth_uses_time_window_and_ignores_missing(self):
        self.assertIsNone(comparison([{"ts": self.now-3600, "ccu": 100}, {"ts": self.now, "ccu": 500}], "ccu", 86400))
        points = [{"ts": self.now-86000, "ccu": 100}, {"ts": self.now-100, "ccu": None}, {"ts": self.now, "ccu": 300}]
        result=comparison(points, "ccu", 86400)
        self.assertEqual(result["pct"], 200)
        self.assertEqual(result["delta"], 200)

    def test_rank_scope_change_is_not_growth(self):
        points = [{"ts": self.now-86400, "wishlist_rank": 100, "rank_scope": "US"}, {"ts": self.now, "wishlist_rank": 50, "rank_scope": "DE"}]
        self.assertIsNone(comparison(points, "wishlist_rank", 86400))

    def test_small_spike_is_not_breakout(self):
        history = [{"ts": self.now-86400, "ccu": 2}, {"ts": self.now, "ccu": 10}]
        game = enrich(self.row, history, DEFAULTS, self.now)
        self.assertFalse(game["breakout"])
        self.assertTrue(game["date_is_proxy"])

    def test_sustained_and_stale(self):
        history=[{"ts": self.now-86400, "ccu": 100}, {"ts": self.now-7200, "ccu": 230}, {"ts": self.now-3600, "ccu": 240}, {"ts": self.now, "ccu": 300}]
        g=enrich(self.row,history,DEFAULTS,self.now)
        self.assertTrue(g["breakout"])
        self.assertTrue(g["sustained"])
        stale=enrich(self.row,history,DEFAULTS,self.now+20000)
        self.assertFalse(stale["breakout"])
        self.assertTrue(stale["stale"])
        self.assertEqual(meta_summary([stale], DEFAULTS), [])

    def test_missing_ccu_not_zero_and_stems_not_substrings(self):
        g=enrich(self.row,[{"ts": self.now, "ccu": None}],DEFAULTS,self.now)
        self.assertIsNone(g["latest"]["ccu"])
        self.assertFalse(g["breakout"])
        self.assertFalse(any(m["id"]=='incremental' for m in classify({"name":"The Bridal Path"})))
        self.assertEqual(classify({"name":"Test", "description":None, "categories":None}), [])

    def test_release_dates(self):
        self.assertEqual(parse_release("Oct 22, 2026"), "2026-10-22")
        self.assertEqual(parse_release("22 Oct, 2026"), "2026-10-22")
        for value in ("Q4 2026", "2027", "Coming soon", None):
            self.assertIsNone(parse_release(value))

    def test_validate_settings(self):
        for invalid in ({"auto_scan":"false"}, {"steam_limit":True}, {"country":"ZZZ"}, {"interval_minutes":0}):
            with self.assertRaises(ValueError): validate_settings(invalid)
        self.assertEqual(validate_settings({"device":"high_end_phone"})["device"],"high_end_phone")

    def test_verified_steam_tags_do_not_mix_scifi_space_or_autobattler(self):
        mechanics=classify({'name':'Test','tag_ids':[3942,1755,4486,1084988]})
        ids={m['id'] for m in mechanics}
        self.assertNotIn('obby',ids)
        self.assertNotIn('social',ids)
        self.assertNotIn('extraction',ids)
        self.assertIn('auto_battler',ids)
        self.assertEqual(classify({'name':'Test','tag_ids':[599]}),[])


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/"test.sqlite3")
    def tearDown(self): self.temp.cleanup()

    def test_snapshots_deduplicate_without_losing_notes(self):
        meta={"platform":"steam", "app_id":"1", "name":"Test"}
        ts=time.time()
        self.db.save(meta,{"ccu":100},ts-1000)
        self.db.edit("steam:1", {"watched":True,"note":"idea"})
        self.db.save(meta,{"ccu":200},ts-500)
        self.db.save(meta,{"ccu":300},ts)
        rows=self.db.games()
        self.assertEqual(rows[0]["note"],"idea")
        self.assertEqual(rows[0]["watched"],1)
        self.assertEqual(len(self.db.history("steam:1")),2)
        self.assertEqual(self.db.history("steam:1")[-1]["ccu"],300)

    def test_steam_api_failure_preserves_unknown(self):
        client=Mock()
        def get(url,params=None):
            if 'appdetails' in url:return {'1':{'success':True,'data':{'name':'Test','type':'game','release_date':{'coming_soon':False,'date':'Oct 22, 2026'}}}}
            if 'GetNumber' in url:return {'response':{'result':42}}
            return {'query_summary':{'total_reviews':0}}
        client.get.side_effect=get
        report=Mock()
        SteamSource(client,self.db).collect_one('1',{},DEFAULTS,report)
        self.assertIsNone(self.db.history('steam:1')[-1]['ccu'])
        self.assertIsNone(self.db.history('steam:1')[-1]['positive_pct'])

    def test_roblox_manual_uses_universe_not_place_id(self):
        client=Mock()
        client.get.return_value={'universeId':123}
        self.assertEqual(resolve_game(client,'https://www.roblox.com/games/456/Name'),('roblox','123'))
        self.assertEqual(resolve_game(client,'steam:730'),('steam','730'))
        with self.assertRaises(ValueError):resolve_game(client,'https://evil.example/123')


class ParserTests(unittest.TestCase):
    def test_steam_markup(self):
        parser=SteamRows()
        parser.feed('<a class="search_result_row" data-ds-appid="123" data-ds-tagids="[1662]"><img src="https://example.test/img"><span class="title">Test &amp; Game</span><div class="search_released">Oct 22, 2026</div><span data-tooltip-html="85% of the 3,756 user reviews are positive."></span></a>')
        self.assertEqual(parser.rows[0]['title'],'Test & Game')
        self.assertEqual(parser.rows[0]['release_text'],'Oct 22, 2026')
        self.assertEqual(parser.rows[0]['tag_ids'],[1662])
        self.assertEqual(parser.rows[0]['review_count'],3756)
        self.assertEqual(parser.rows[0]['review_pct'],85)

    def test_live_probe_fixture_when_available(self):
        path=Path('data/probes/steam_wishlist.json')
        if not path.exists():self.skipTest('Optional live fixture absent')
        parser=SteamRows()
        parser.feed(json.loads(path.read_text(encoding='utf8'))['results_html'])
        self.assertGreaterEqual(len(parser.rows),5)
        self.assertTrue(all(row['app_id'].isdigit() and row['title'] for row in parser.rows))


if __name__=='__main__':unittest.main()

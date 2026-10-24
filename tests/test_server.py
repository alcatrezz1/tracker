import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from radar.db import Database
from radar.locking import ProcessLock
from radar.server import make_server


class FakeCollector:
    next_scan=0
    def status(self):return {"running":False}
    def start(self,added=None):return True


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.db=Database(Path(cls.temp.name)/'test.sqlite3')
        cls.db.save({'platform':'steam','app_id':'1','name':'=HYPERLINK("bad")','url':'https://store.steampowered.com/app/1/','description':None,'upcoming':False,'date_kind':'steam_release','release_date':'2026-10-01','genres':[]}, {'ccu':100,'positive_pct':85})
        cls.server=make_server(cls.db,FakeCollector(),0)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()

    def request(self,path,payload=None,headers=None):
        h={'Content-Type':'application/json'} if payload is not None else {}
        h.update(headers or {})
        req=urllib.request.Request(self.base+path, data=json.dumps(payload).encode() if payload is not None else None,headers=h)
        return urllib.request.urlopen(req,timeout=10)

    def test_dashboard_null_description(self):
        with self.request('/api/dashboard') as r:
            value=json.load(r)
        self.assertEqual(value['counts']['total'],1)
        self.assertEqual(value['games'][0]['latest']['ccu'],100)

    def test_note_and_watch_persist(self):
        with self.request('/api/game',{'key':'steam:1','note':'Prototype','watched':True}) as r:self.assertEqual(r.status,200)
        self.assertEqual(self.db.games()[0]['note'],'Prototype')

    def test_csv_formula_is_escaped(self):
        with self.request('/api/export.csv') as r:
            text=r.read().decode('utf-8-sig')
        self.assertIn("'=HYPERLINK",text)

    def test_origin_and_host_are_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.request('/api/settings',{'auto_scan':False},{'Origin':'https://example.test'})
        self.assertEqual(err.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as err:
            self.request('/api/dashboard',headers={'Host':'example.test'})
        self.assertEqual(err.exception.code,403)

    def test_lock_released(self):
        path=Path(self.temp.name)/'test.lock'
        first,second=ProcessLock(path),ProcessLock(path)
        self.assertTrue(first.acquire())
        self.assertFalse(second.acquire())
        first.release()
        self.assertTrue(second.acquire())
        second.release()


if __name__=='__main__':unittest.main()

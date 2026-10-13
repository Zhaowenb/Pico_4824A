import json
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from pico4824a.web import PicoWebHandler, ThreadingHTTPServer, WebControlState

class Updater:
    def __init__(self):self.phase='current';self.calls=[]
    def snapshot(self):return {'phase':self.phase,'current':'abc','auto_check':False}
    def check(self):self.calls.append('check');return self.snapshot()
    def apply(self):self.calls.append('apply');return self.snapshot()
    def preferences(self,raw):self.calls.append(raw);return self.snapshot()

class SoftwareUpdateHTTPTests(unittest.TestCase):
    def setUp(self):
        self.server=ThreadingHTTPServer(('127.0.0.1',0),PicoWebHandler)
        self.server.control=WebControlState();self.server.software_updates=Updater()
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_address[1])
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def post(self,path,headers=None):
        r=Request(self.base+path,data=b'{}',headers={'Content-Type':'application/json',**(headers or {})})
        try:
            with urlopen(r) as response:return response.status,json.load(response)
        except HTTPError as error:return error.code,json.load(error)
    def test_status_and_explicit_check(self):
        with urlopen(self.base+'/api/software-update') as r:self.assertEqual(json.load(r)['current'],'abc')
        status,_=self.post('/api/software-update/check',{'X-WaveGuard-Update':'1','Origin':self.base})
        self.assertEqual(status,202);self.assertEqual(self.server.software_updates.calls,['check'])
    def test_cross_origin_and_simple_requests_rejected(self):
        status,_=self.post('/api/software-update/apply')
        self.assertEqual(status,403)
        status,_=self.post('/api/software-update/apply',{'X-WaveGuard-Update':'1','Origin':'http://other.invalid'})
        self.assertEqual(status,403);self.assertEqual(self.server.software_updates.calls,[])
    def test_instrument_commands_blocked_while_updating(self):
        self.server.software_updates.phase='installing'
        status,payload=self.post('/api/capture')
        self.assertEqual(status,409);self.assertIn('更新',payload['error'])
        self.assertIsNone(self.server.control._worker_thread)

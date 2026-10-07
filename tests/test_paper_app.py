import csv
import io
import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from paper_app.server import Jobs


def dataset():
    f=io.StringIO(); fields=['timestamp','contract','expiry_date','open','high','low','close','volume','forecast_close']
    w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
    for i in range(60):
        c=100+i*.25
        w.writerow(dict(timestamp=(datetime(2026,1,5,13,tzinfo=timezone.utc)+timedelta(minutes=5*i)).isoformat(),
                        contract='TECHNICAL_TEST',expiry_date='2099-01-01',open=c-.25,high=c+.5,low=c-.5,close=c,
                        volume=100,forecast_close=130 if i==49 else ''))
    return f.getvalue()

class PanelTests(unittest.TestCase):
    def test_end_to_end_and_duplicate_reuse(self):
        with tempfile.TemporaryDirectory() as d:
            jobs=Jobs(d)
            ident=jobs.start(dataset(),{'mode':'import'})['id']
            deadline=time.monotonic()+10
            while jobs.active and time.monotonic()<deadline: time.sleep(.01)
            result=jobs.detail(ident)
            self.assertEqual(result['status'],'completed',result['log'])
            self.assertEqual(set(result['results']),{'fixed','trailing'})
            reused=jobs.start(dataset(),{'mode':'import'})
            self.assertTrue(reused['reused']); self.assertEqual(reused['id'],ident)

    def test_restart_marks_incomplete_and_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            jobs=Jobs(d)
            with jobs.connect() as c:
                c.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?)',('a'*32,'f','running','x','x','','{}'))
            restored=Jobs(d)
            self.assertEqual(restored.status('a'*32),'interrupted')
            with self.assertRaises(ValueError): restored.detail('../input.csv')

    def test_invalid_upload_and_risk(self):
        with tempfile.TemporaryDirectory() as d:
            jobs=Jobs(d)
            with self.assertRaises(ValueError): jobs.start(dataset(),{'balance':'nan'})
            with self.assertRaises(ValueError): jobs.start(dataset(),{'slippage':1.5})
            with self.assertRaises(ValueError): jobs.start('timestamp\n',{})
            self.assertIsNone(jobs.active)

    def test_stop_interrupts_worker(self):
        from unittest.mock import patch
        import threading
        class Process:
            def __init__(self,*a,**kw): self.event=threading.Event(); self.code=None
            def wait(self): self.event.wait(5); return self.code or 0
            def poll(self): return self.code
            def kill(self): self.code=-9; self.event.set()
        with tempfile.TemporaryDirectory() as d, patch('paper_app.server.subprocess.Popen',Process):
            jobs=Jobs(d); ident=jobs.start(dataset(),{'mode':'import'})['id']
            deadline=time.monotonic()+2
            while jobs.process is None and time.monotonic()<deadline: time.sleep(.01)
            self.assertEqual(jobs.stop()['status'],'stopping')
            while jobs.active and time.monotonic()<deadline: time.sleep(.01)
            self.assertEqual(jobs.status(ident),'stopped')
            self.assertEqual(jobs.detail(ident)['results'],{})

if __name__=='__main__': unittest.main()

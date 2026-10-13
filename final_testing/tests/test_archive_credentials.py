import json
import os
import threading
import unittest
import uuid
from tempdesk.archive_credentials import ArchiveConnection, WindowsCredentialStore

class MemoryStore:
    def __init__(self, value=None): self.value=value;self.writes=0
    def save(self, value): self.value=value;self.writes+=1
    def load(self): return self.value
    def delete(self): self.value=None

class ArchiveCredentialTests(unittest.TestCase):
    def test_failed_probe_never_saves(self):
        store=MemoryStore()
        def rejected(value): raise ConnectionError('rejected')
        c=ArchiveConnection(rejected,store)
        with self.assertRaises(ConnectionError):c.connect('test','fake',True)
        self.assertEqual(store.writes,0);self.assertIsNone(c.get())

    def test_remember_restore_and_export_redaction(self):
        store=MemoryStore();seen=[]
        c=ArchiveConnection(seen.append,store)
        self.assertTrue(c.connect('synthetic-user','synthetic-password',True)['credentials_saved'])
        second=ArchiveConnection(seen.append,store);second.restore()
        self.assertEqual(second.get(),('synthetic-user','synthetic-password'))
        self.assertEqual(second.metadata()['archive_connection'],'CONNECTED')
        self.assertEqual(len(seen),2)
        public=json.dumps(second.metadata())
        self.assertNotIn('synthetic-user',public);self.assertNotIn('synthetic-password',public)

    def test_saved_invalid_login_not_admitted(self):
        def rejected(value): raise ConnectionError('rejected')
        c=ArchiveConnection(rejected,MemoryStore(('test','fake')));c.restore()
        self.assertIsNone(c.get());self.assertTrue(c.metadata()['archive_credentials_saved'])
        self.assertEqual(c.metadata()['archive_connection'],'SAVED_CONNECTION_UNAVAILABLE')

    def test_forget_only_our_store(self):
        store=MemoryStore(('test','fake'));c=ArchiveConnection(lambda _:None,store);c.restore()
        c.disconnect(forget=True)
        self.assertIsNone(c.get());self.assertIsNone(store.value)
        self.assertFalse(c.metadata()['archive_credentials_saved'])

    def test_memory_only_does_not_require_windows(self):
        class NoVault(MemoryStore):
            def save(self,v): raise OSError('unavailable')
            def delete(self): raise OSError('unavailable')
        c=ArchiveConnection(lambda _:None,NoVault())
        c.connect('test','fake',False);self.assertIsNotNone(c.get())
        self.assertFalse(c.metadata()['archive_credentials_saved'])

    def test_late_restore_cannot_undo_forget(self):
        loaded=threading.Event();resume=threading.Event()
        class Delayed(MemoryStore):
            def load(self): loaded.set();resume.wait(3);return ('test','fake')
        c=ArchiveConnection(lambda _:None,Delayed())
        worker=threading.Thread(target=c.restore);worker.start();self.assertTrue(loaded.wait(3))
        c.disconnect(forget=True);resume.set();worker.join(3)
        self.assertIsNone(c.get());self.assertEqual(c.metadata()['archive_connection'],'DISCONNECTED')

    @unittest.skipUnless(os.name=='nt','Windows native store')
    def test_real_windows_store_roundtrip_synthetic_only(self):
        store=WindowsCredentialStore('TempDesk:test-only:'+str(uuid.uuid4()))
        try:
            self.assertIsNone(store.load());store.save(('synthetic-test','synthetic-test-password'))
            self.assertEqual(store.load(),('synthetic-test','synthetic-test-password'))
            store.delete();self.assertIsNone(store.load())
        finally:store.delete()

if __name__=='__main__':unittest.main()

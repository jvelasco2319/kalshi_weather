import json,tempfile,unittest,os
from pathlib import Path
from unittest.mock import patch
from tempdesk.offline_sources import atomic_json

class AtomicWriteTests(unittest.TestCase):
    def test_windows_transient_reader_lock_is_retried_without_truncation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            path=Path(folder)/'progress.json';path.write_text('{"old":true}')
            original=os.replace;calls=[]
            def replace(a,b):
                calls.append(1)
                if len(calls)==1:
                    self.assertEqual(json.loads(path.read_text()),{'old':True})
                    raise PermissionError('synthetic reader lock')
                original(a,b)
            with patch('tempdesk.offline_sources.os.replace',side_effect=replace),patch('tempdesk.offline_sources.time.sleep'):
                atomic_json(path,{'complete':True})
            self.assertEqual(len(calls),2);self.assertEqual(json.loads(path.read_text()),{'complete':True})

    def test_persistent_failure_retains_old_history_and_new_temporary_evidence(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            path=Path(folder)/'history.json';path.write_text('{"old":true}')
            with patch('tempdesk.offline_sources.os.replace',side_effect=PermissionError('lock')),patch('tempdesk.offline_sources.time.sleep'):
                with self.assertRaises(PermissionError):atomic_json(path,{'new':True})
            self.assertEqual(json.loads(path.read_text()),{'old':True})
            self.assertEqual(len(list(Path(folder).glob('*.tmp'))),1)

if __name__=='__main__':unittest.main()

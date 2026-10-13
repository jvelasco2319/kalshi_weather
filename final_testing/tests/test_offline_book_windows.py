import unittest
from datetime import datetime,timedelta
from tempdesk.offline_sources import replay_book_window

class BookWindowTests(unittest.TestCase):
    def test_ny_every_native_decision_and_arrival_fresh_interval_is_retained(self):
        lo,hi,predicate=replay_book_window('ny','2026-10-12')
        self.assertEqual(lo,'2026-10-12 13:59:55');self.assertEqual(hi,'2026-10-12 17:00:05')
        self.assertIn('toSecond(orderbook_snapshots.timestamp)',predicate)
        self.assertNotIn('toSecond(timestamp)',predicate) # SELECT alias is String.
        start=datetime.fromisoformat('2026-10-12T14:00:00')
        for minute in range(181):
            for offset in (0,5):
                at=start+timedelta(minutes=minute,seconds=offset)
                for age in range(6):
                    event=at-timedelta(seconds=age)
                    self.assertTrue(event.second>=55 or event.second<=5)
                    self.assertTrue(datetime.fromisoformat(lo)<=event<=datetime.fromisoformat(hi))

    def test_miami_and_phoenix_keep_all_required_native_fresh_intervals(self):
        lo,hi,predicate=replay_book_window('miami','2026-10-12')
        self.assertEqual((lo,hi,predicate),('2026-10-12 13:59:55','2026-10-12 14:00:05',''))
        lo,hi,predicate=replay_book_window('phoenix','2026-10-12')
        for clock in ('17:00','17:15','17:30','18:00','19:00'):
            at=datetime.fromisoformat('2026-10-12T'+clock+':00')
            self.assertIn((at-timedelta(seconds=5)).strftime('%Y-%m-%d %H:%M:%S'),predicate)
            self.assertIn((at+timedelta(seconds=5)).strftime('%Y-%m-%d %H:%M:%S'),predicate)
        self.assertNotIn('state',predicate);self.assertNotIn('continuity',predicate)

    def test_la_keeps_longer_lookback_and_all_native_exit_minutes(self):
        self.assertEqual(replay_book_window('la','2026-10-12'),('2026-10-12 16:29:55','2026-10-12 20:00:05',''))

if __name__=='__main__':unittest.main()

import threading
import unittest
import uuid

from lunete.ids import MAX_AHEAD_MS, Hlc, _Uuid7, is_item_id, is_uuid7, item_uuid5, parse_stamp, uuid7, uuid7_ms


class Clock:
    def __init__(self, ms):
        self.ms = ms

    def __call__(self):
        return self.ms


class Uuid7Test(unittest.TestCase):
    def test_version_variant_and_time(self):
        clock = Clock(1_795_000_000_123)
        u = _Uuid7(clock)()
        self.assertEqual(u.version, 7)
        self.assertEqual(u.variant, uuid.RFC_4122)
        self.assertEqual(uuid7_ms(u), 1_795_000_000_123)
        self.assertTrue(is_uuid7(str(u)))

    def test_sorted_within_one_millisecond(self):
        make = _Uuid7(Clock(1_795_000_000_000))
        ids = [make() for _ in range(3000)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 3000)

    def test_sorted_when_the_clock_steps_back(self):
        clock = Clock(1_795_000_005_000)
        make = _Uuid7(clock)
        first = [make() for _ in range(5)]
        clock.ms -= 3_000  # NTP pulls the clock back three seconds
        later = [make() for _ in range(5)]
        self.assertEqual(first + later, sorted(first + later))

    def test_counter_overflow_borrows_the_next_millisecond(self):
        make = _Uuid7(Clock(1_795_000_000_000))
        ids = [make() for _ in range(9000)]  # more than 4096 in one ms, from any random start
        self.assertEqual(ids, sorted(ids))
        self.assertGreater(uuid7_ms(ids[-1]), 1_795_000_000_000)

    def test_threads_get_unique_ids(self):
        out, lock = [], threading.Lock()

        def work():
            got = [uuid7() for _ in range(500)]
            with lock:
                out.extend(got)
        ts = [threading.Thread(target=work) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(set(out)), 4000)

    def test_is_uuid7_rejects_other_forms(self):
        u = str(uuid7())
        self.assertFalse(is_uuid7(u.upper()))
        self.assertFalse(is_uuid7(str(uuid.uuid4())))
        self.assertFalse(is_uuid7("not an id"))
        self.assertFalse(is_uuid7(None))


class HlcTest(unittest.TestCase):
    def test_local_stamps_sort_and_survive_a_clock_step_back(self):
        clock = Clock(1_795_000_000_000)
        h = Hlc("laptop", clock)
        a = h.now()
        b = h.now()
        clock.ms -= 10_000
        c = h.now()
        self.assertLess(a, b)
        self.assertLess(b, c)
        self.assertEqual(parse_stamp(c)[0], 1_795_000_000_000)

    def test_a_stamp_from_ahead_moves_the_clock_forward(self):
        clock = Clock(1_795_000_000_000)
        h = Hlc("laptop", clock)
        h.seen("1795000009999.000004.desktop")
        nxt = h.now()
        self.assertGreater(nxt, "1795000009999.000004.desktop")

    def test_a_stamp_too_far_ahead_is_refused_and_leaves_the_clock_alone(self):
        clock = Clock(1_795_000_000_000)
        h = Hlc("laptop", clock)
        before = h.now()
        far = f"{1_795_000_000_000 + MAX_AHEAD_MS + 1:013d}.000000.desktop"
        with self.assertRaisesRegex(ValueError, "clock ahead"):
            h.seen(far)
        self.assertLess(h.now(), far)
        self.assertGreater(h.now(), before)
        h.seen(f"{1_795_000_000_000 + MAX_AHEAD_MS:013d}.000000.desktop")  # exactly at the limit: accepted

    def test_seeding_from_the_store_skips_the_cap(self):
        h = Hlc("laptop", Clock(1_795_000_000_000))
        stored = "1795999999999.000007.laptop"
        h.seen(stored, max_ahead_ms=None)
        self.assertGreater(h.now(), stored)

    def test_stamps_sort_as_numbers(self):
        self.assertLess("0000000000009.000000.a", "0000000000010.000000.a")

    def test_bad_input_names_the_format(self):
        with self.assertRaisesRegex(ValueError, "13-digit ms"):
            parse_stamp("12.3.x")
        with self.assertRaisesRegex(ValueError, "letters, digits"):
            Hlc("my laptop")


class ItemIdTest(unittest.TestCase):
    ORG = "019a8f3e-1c2b-7d4e-8f00-0123456789ab"

    def test_derived_id_is_stable_v5_and_depends_on_every_part(self):
        a = item_uuid5(self.ORG, "git", "github.com/x/y@" + "a" * 40)
        self.assertEqual(a, item_uuid5(self.ORG, "git", "github.com/x/y@" + "a" * 40))
        self.assertEqual(a.version, 5)
        self.assertEqual(a, uuid.uuid5(uuid.UUID(self.ORG), "git\ngithub.com/x/y@" + "a" * 40))
        self.assertNotEqual(a, item_uuid5(self.ORG, "ics", "github.com/x/y@" + "a" * 40))
        other_org = "019a8f3e-1c2b-7d4e-8f00-0123456789ac"
        self.assertNotEqual(a, item_uuid5(other_org, "git", "github.com/x/y@" + "a" * 40))
        self.assertTrue(is_item_id(str(a)))

    def test_derived_id_refuses_bad_parts(self):
        with self.assertRaisesRegex(ValueError, "UUIDv7"):
            item_uuid5("local", "git", "x")
        with self.assertRaisesRegex(ValueError, "one line"):
            item_uuid5(self.ORG, "gi\nt", "x")
        with self.assertRaisesRegex(ValueError, "external_id is empty"):
            item_uuid5(self.ORG, "git", "")

    def test_item_ids_are_v7_or_v5_only(self):
        self.assertTrue(is_item_id(str(uuid7())))
        self.assertFalse(is_item_id(str(uuid.uuid4())))
        self.assertFalse(is_item_id(str(uuid7()).upper()))
        self.assertFalse(is_item_id("local"))


if __name__ == "__main__":
    unittest.main()

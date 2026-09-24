import datetime as dt
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

TMP = tempfile.mkdtemp()
os.environ["TENBIS_CREDIT_HOME"] = TMP

from tenbis_credit import api, config, runner, scheduler  # noqa: E402


def report(*cards):
    return {"Success": True, "Errors": [], "Data": {"moneycards": list(cards)}}


def card(available, suffix="1234", enc="ENC1", credit=False, enabled=True):
    return {"isTenbisCredit": credit, "cardDeleted": False, "cardSuffix": suffix,
            "encryptedMoneycardID": enc,
            "tenbisCreditConversion": {"isEnabled": enabled, "availableAmount": available}}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Serves queued responses per URL prefix and records every request."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.requests = []

    def open(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        self.requests.append((req.get_method(), req.full_url, body, dict(req.header_items())))
        for prefix, queue in self.routes.items():
            if req.full_url.startswith(prefix):
                item = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(item, Exception):
                    raise item
                return FakeResponse(b"" if item is None else json.dumps(item).encode())
        raise AssertionError(f"unexpected request {req.full_url}")


def make_client(routes):
    opener = FakeOpener(routes)
    return api.Client(os.path.join(TMP, "cookies.txt"), opener=opener), opener


def http_error(code, body=b"{}"):
    return urllib.error.HTTPError("u", code, "err", {}, io.BytesIO(body))


CFG = dict(config.DEFAULTS)
THURSDAY = dt.date(2026, 9, 24)
FRIDAY = dt.date(2026, 9, 25)


class FormatAmount(unittest.TestCase):
    def test_strings_like_the_site(self):
        self.assertEqual(api.format_amount(35), "35")
        self.assertEqual(api.format_amount(35.5), "35.5")
        self.assertEqual(api.format_amount(12.345), "12.35")


class Cards(unittest.TestCase):
    def test_picks_only_convertible_budget_cards(self):
        c, _ = make_client({api.ENDPOINTS["report"]: [report(
            card(35), card(0, "5678", "ENC2", credit=True), card(10, "1111", "ENC3", enabled=False))]})
        cards = c.convertible_cards()
        self.assertEqual([(x.suffix, x.available) for x in cards], [("1234", 35.0)])


class Schedule(unittest.TestCase):
    def test_skips_unscheduled_days(self):
        self.assertTrue(runner.scheduled_today(CFG, THURSDAY)[0])
        self.assertFalse(runner.scheduled_today(CFG, FRIDAY)[0])

    def test_monthly_uses_last_scheduled_day(self):
        # Oct 2026 ends Sat 31; Fri 30 is not a work day -> Thu 29.
        self.assertEqual(runner.last_scheduled_day(dt.date(2026, 10, 5), CFG["days"]), dt.date(2026, 10, 29))
        monthly = {**CFG, "mode": "monthly"}
        self.assertFalse(runner.scheduled_today(monthly, dt.date(2026, 10, 28))[0])
        self.assertTrue(runner.scheduled_today(monthly, dt.date(2026, 10, 29))[0])

    def test_cron_line(self):
        line = scheduler.cron_line(CFG)
        self.assertTrue(line.startswith("0 22 * * 0,1,2,3,4 "))
        self.assertIn("-m tenbis_credit run", line)
        self.assertTrue(line.endswith(scheduler.CRON_MARKER))

    def test_windows_args(self):
        args = scheduler.windows_create_args(CFG)
        self.assertEqual(args[args.index("/D") + 1], "SUN,MON,TUE,WED,THU")
        self.assertEqual(args[args.index("/ST") + 1], "22:00")

    def test_launchd_plist(self):
        import plistlib
        plist = plistlib.loads(scheduler.launchd_plist(CFG))
        self.assertEqual([x["Weekday"] for x in plist["StartCalendarInterval"]], [0, 1, 2, 3, 4])
        self.assertEqual(plist["ProgramArguments"][-3:], ["-m", "tenbis_credit", "run"])


class Run(unittest.TestCase):
    def routes(self, before, after=0):
        return {api.ENDPOINTS["refresh"]: [None],
                api.ENDPOINTS["report"]: [report(card(before)), report(card(after))],
                api.ENDPOINTS["load_credit"]: [None]}

    def test_moves_whole_available_amount(self):
        c, opener = make_client(self.routes(35))
        events = runner.run(c, CFG, today=THURSDAY)
        self.assertEqual(events[0]["action"], "moved")
        self.assertEqual(events[0]["left"], 0)
        method, url, body, headers = next(r for r in opener.requests if "LoadTenbisCredit" in r[1])
        self.assertEqual(method, "PATCH")
        self.assertEqual(body, {"amount": "35", "encryptedMoneycardIdToCharge": "ENC1"})
        self.assertEqual(headers.get("X-app-type"), "web")

    def test_refreshes_session_first(self):
        c, opener = make_client(self.routes(35))
        runner.run(c, CFG, today=THURSDAY)
        self.assertIn("RefreshToken", opener.requests[0][1])

    def test_respects_cap(self):
        c, opener = make_client(self.routes(1500))
        runner.run(c, {**CFG, "max_amount_per_run": 100}, today=THURSDAY)
        body = next(r[2] for r in opener.requests if "LoadTenbisCredit" in r[1])
        self.assertEqual(body["amount"], "100")

    def test_nothing_left(self):
        c, opener = make_client(self.routes(0))
        self.assertEqual(runner.run(c, CFG, today=THURSDAY)[0]["action"], "skip")
        self.assertFalse(any("LoadTenbisCredit" in r[1] for r in opener.requests))

    def test_dry_run_moves_nothing(self):
        c, opener = make_client(self.routes(35))
        self.assertEqual(runner.run(c, CFG, dry_run=True, today=THURSDAY)[0]["would_move"], 35)
        self.assertFalse(any("LoadTenbisCredit" in r[1] for r in opener.requests))

    def test_friday_does_nothing(self):
        c, opener = make_client(self.routes(35))
        self.assertEqual(runner.run(c, CFG, today=FRIDAY)[0]["action"], "skip")
        self.assertEqual(opener.requests, [])

    def test_expired_session(self):
        c, _ = make_client({api.ENDPOINTS["refresh"]: [http_error(401)]})
        with self.assertRaises(api.SessionExpired):
            runner.run(c, CFG, today=THURSDAY)

    def test_rejected_transfer_is_an_error(self):
        routes = self.routes(35)
        routes[api.ENDPOINTS["load_credit"]] = [http_error(400, json.dumps(
            {"description": "טעינת קרדיט נכשלה", "code": "INVALID_REQUEST"}).encode())]
        c, _ = make_client(routes)
        with self.assertRaisesRegex(api.TenbisError, "טעינת קרדיט נכשלה"):
            runner.run(c, CFG, today=THURSDAY)


class Login(unittest.TestCase):
    def test_send_and_verify(self):
        auth = {"authenticationToken": "T", "sendingMethod": "Phone"}
        c, opener = make_client({api.ENDPOINTS["send_code"]: [{"Success": True, "Data": {"codeAuthenticationData": auth}}],
                                 api.ENDPOINTS["verify_code"]: [{"Success": True, "Data": {}}]})
        self.assertEqual(c.send_code("a@b.c"), auth)
        c.verify_code("a@b.c", auth, " 12345 ")
        body = opener.requests[-1][2]
        self.assertEqual((body["authenticationToken"], body["authenticationCode"]), ("T", "12345"))

    def test_unknown_email(self):
        c, _ = make_client({api.ENDPOINTS["send_code"]: [{"Success": True, "Data": {}}]})
        with self.assertRaises(api.TenbisError):
            c.send_code("nobody@example.com")


class Config(unittest.TestCase):
    def test_rejects_bad_values(self):
        for bad in ({"mode": "hourly"}, {"days": ["funday"]}, {"time": "25:00"}):
            with self.assertRaises(ValueError):
                config.validate({**CFG, **bad})

    def test_files_are_private(self):
        config.save(dict(CFG))
        if os.name == "posix":
            self.assertEqual(os.stat(config.config_path()).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()

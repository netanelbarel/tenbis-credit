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


def card(available, suffix="1234", enc="ENC1", credit=False, enabled=True,
         daily=100, weekly=0, monthly=2300, settings=None):
    return {"isTenbisCredit": credit, "cardDeleted": False, "cardSuffix": suffix,
            "encryptedMoneycardID": enc,
            "limitation": {"daily": daily, "weekly": weekly, "monthly": monthly},
            "tenbisCreditConversion": {"isEnabled": enabled, "availableAmount": available,
                                       "tenbisCreditSettings": settings}}


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


class BudgetTypes(unittest.TestCase):
    """Companies set budgets differently; each card moves when its budget resets."""

    def test_period_from_company_limits(self):
        self.assertEqual(api.Card("E", "1", 0, daily_limit=100, monthly_limit=2300).period, "daily")
        self.assertEqual(api.Card("E", "1", 0, weekly_limit=500).period, "weekly")
        self.assertEqual(api.Card("E", "1", 0, monthly_limit=1500).period, "monthly")
        self.assertEqual(api.Card("E", "1", 0).period, "monthly")  # unknown -> safest

    def test_parses_limits_and_auto_credit(self):
        settings = {"disableAutoCreditOption": False, "autoCreditSubscribed": True}
        c, _ = make_client({api.ENDPOINTS["report"]: [report(card(35, daily=0, weekly=400, settings=settings))]})
        (parsed,) = c.convertible_cards()
        self.assertEqual((parsed.period, parsed.weekly_limit), ("weekly", 400))
        self.assertTrue(parsed.auto_credit_offered and parsed.auto_credit_on)

    def test_monthly_budget_not_moved_mid_month(self):
        monthly_card = api.Card("E", "1", 1200, monthly_limit=1500)
        self.assertFalse(runner.card_due(monthly_card, CFG, THURSDAY)[0])  # Sep 24
        self.assertTrue(runner.card_due(monthly_card, CFG, dt.date(2026, 9, 30))[0])  # last work day

    def test_weekly_budget_moved_on_thursday(self):
        weekly_card = api.Card("E", "1", 300, weekly_limit=500)
        self.assertFalse(runner.card_due(weekly_card, CFG, dt.date(2026, 9, 22))[0])  # Tuesday
        self.assertTrue(runner.card_due(weekly_card, CFG, THURSDAY)[0])

    def test_leaves_cards_with_10bis_auto_credit_alone(self):
        self.assertFalse(runner.card_due(api.Card("E", "1", 35, daily_limit=100, auto_credit_on=True),
                                         CFG, THURSDAY)[0])

    def test_force_cannot_empty_a_monthly_budget_early(self):
        routes = {api.ENDPOINTS["refresh"]: [None],
                  api.ENDPOINTS["report"]: [report(card(1200, daily=0, monthly=1500))]}
        c, opener = make_client(routes)
        events = runner.run(c, CFG, force=True, today=FRIDAY)
        self.assertEqual(events[0]["action"], "skip")
        self.assertFalse(any("LoadTenbisCredit" in r[1] for r in opener.requests))

    def test_mixed_cards_move_independently(self):
        routes = {api.ENDPOINTS["refresh"]: [None],
                  api.ENDPOINTS["report"]: [report(card(35), card(900, "9999", "ENC9", daily=0, monthly=1500)),
                                            report(card(0), card(900, "9999", "ENC9", daily=0, monthly=1500))],
                  api.ENDPOINTS["load_credit"]: [None]}
        c, opener = make_client(routes)
        events = runner.run(c, CFG, today=THURSDAY)
        self.assertEqual([(e["card"], e["action"]) for e in events], [("1234", "moved"), ("9999", "skip")])
        loads = [r[2] for r in opener.requests if "LoadTenbisCredit" in r[1]]
        self.assertEqual(loads, [{"amount": "35", "encryptedMoneycardIdToCharge": "ENC1"}])


class Schedule(unittest.TestCase):
    def test_skips_unscheduled_days(self):
        self.assertTrue(runner.scheduled_today(CFG, THURSDAY)[0])
        self.assertFalse(runner.scheduled_today(CFG, FRIDAY)[0])

    def test_monthly_uses_last_scheduled_day(self):
        # Oct 2026 ends Sat 31; Fri 30 is not a work day -> Thu 29.
        self.assertEqual(runner.last_scheduled_day(dt.date(2026, 10, 5), CFG["days"]), dt.date(2026, 10, 29))
        daily_card = api.Card("E", "1", 35, daily_limit=100)
        monthly = {**CFG, "mode": "monthly"}
        self.assertFalse(runner.card_due(daily_card, monthly, dt.date(2026, 10, 28))[0])
        self.assertTrue(runner.card_due(daily_card, monthly, dt.date(2026, 10, 29))[0])

    def test_week_ends_on_last_work_day(self):
        sunday, thursday = dt.date(2026, 9, 20), dt.date(2026, 9, 24)
        for day in (sunday, thursday):
            self.assertEqual(runner.last_scheduled_day_of_week(day, CFG["days"]), thursday)
        self.assertEqual(runner.last_scheduled_day_of_week(sunday, ["sun", "mon"]), dt.date(2026, 9, 21))

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


class LoginPrompt(unittest.TestCase):
    def fake_client(self):
        c = mock.Mock()
        c.send_code.return_value = {"sendingMethod": "Phone"}
        return c

    def test_asks_for_email_with_saved_one_as_default(self):
        from tenbis_credit import cli
        c = self.fake_client()
        answers = iter(["", "12345"])  # Enter keeps the saved email
        with mock.patch.object(cli, "client", return_value=c), \
             mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=lambda p: (prompts.append(p), next(answers))[1]):
            prompts = []
            self.assertTrue(cli.do_login({**CFG, "email": "old@example.com"}, None, None))
        self.assertIn("[old@example.com]", prompts[0])
        c.send_code.assert_called_once_with("old@example.com")

    def test_typed_email_replaces_saved_one(self):
        from tenbis_credit import cli
        c = self.fake_client()
        answers = iter(["new@example.com", "12345"])
        with mock.patch.object(cli, "client", return_value=c), \
             mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", side_effect=lambda p: next(answers)):
            cfg = {**CFG, "email": "old@example.com"}
            cli.do_login(cfg, None, None)
        c.send_code.assert_called_once_with("new@example.com")
        self.assertEqual(cfg["email"], "new@example.com")


class Config(unittest.TestCase):
    def test_old_daily_mode_means_auto(self):
        config.write_private(config.config_path(), json.dumps({"mode": "daily"}))
        self.assertEqual(config.load()["mode"], "auto")

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

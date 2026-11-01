"""Haircut tracing uses exact fractions and retains uncertainty and evidence."""

import copy
import random
import unittest
from fractions import Fraction

from test_sentinel import digest

from sentinel.tracing import TraceError, trace_events

ASSET = "0x" + "11" * 20
OTHER = "0x" + "22" * 20
A, B, C = (f"0x{n:040x}" for n in (1, 2, 3))


def checkpoint():
    return {
        "chain_id": 1,
        "block_number": 0,
        "block_hash": digest(1000),
        "source": "fixture balances",
        "balances": [
            {"address": a, "asset": ASSET, "amount": str(n), "kind": "wallet"}
            for a, n in ((A, 100), (B, 100), (C, 0))
        ],
    }


def seed(amount=40):
    return {
        "claim_id": "claim-1",
        "address": A,
        "asset": ASSET,
        "amount": str(amount),
        "source": "fixture seed",
        "registered_by": "fixture",
        "start_block": 0,
        "end_block": 0,
    }


def event(n, sender, recipient, amount, asset=ASSET):
    return {
        "event_id": f"1:{digest(1000 + n)}:{digest(2000 + n)}:0",
        "block_hash": digest(1000 + n),
        "transaction_hash": digest(2000 + n),
        "block_number": n,
        "timestamp": 100 + n,
        "transaction_index": 0,
        "log_index": 0,
        "asset": asset,
        "sender": sender,
        "recipient": recipient,
        "amount": str(amount),
        "finalized": False,
    }


def rational(value):
    return Fraction(int(value["numerator"]), int(value["denominator"]))


def account(result, address, asset=ASSET):
    return next(
        a for a in result["accounts"] if (a["address"], a["asset"]) == (address, asset)
    )


class TracingTests(unittest.TestCase):
    def test_mixing_and_cycle_use_haircut_not_fifo(self):
        result = trace_events(
            [event(1, A, B, 50), event(2, B, C, 75), event(3, C, A, 30)],
            checkpoint(),
            [seed()],
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(rational(account(result, A)["tainted"]), 24)
        self.assertEqual(rational(account(result, B)["tainted"]), 10)
        self.assertEqual(rational(account(result, C)["tainted"]), 6)
        self.assertEqual(sum(rational(a["tainted"]) for a in result["accounts"]), 40)
        self.assertEqual(
            result["graph"][2]["source_node"], result["graph"][1]["recipient_after"]
        )

    def test_self_send_and_duplicate_seed_do_not_count_twice_or_add_hops(self):
        s = seed()
        duplicate = {**s, "source": "second evidence"}
        result = trace_events(
            [event(1, A, A, 100), event(2, A, C, 100)],
            checkpoint(),
            [s, duplicate],
            max_hops=1,
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(rational(account(result, C)["tainted"]), 40)
        self.assertEqual(len(result["seeds"]), 1)
        self.assertEqual(len(result["seeds"][0]["evidence"]), 2)

    def test_fractional_dust_is_not_rounded_away(self):
        cp = checkpoint()
        cp["balances"][0]["amount"] = "3"
        result = trace_events([event(1, A, C, 1)], cp, [seed(1)])
        self.assertEqual(rational(account(result, C)["tainted"]), Fraction(1, 3))
        self.assertEqual(rational(account(result, A)["tainted"]), Fraction(2, 3))

    def test_missing_balance_and_inconsistent_history_become_unknown(self):
        cp = checkpoint()
        cp["balances"] = [b for b in cp["balances"] if b["address"] != B]
        result = trace_events([event(1, A, B, 50), event(2, B, C, 10)], cp, [seed()])
        self.assertEqual(result["status"], "hold")
        self.assertIsNone(account(result, B)["unknown"])
        self.assertEqual(rational(account(result, C)["unknown"]), 10)
        self.assertEqual(rational(result["claims"][0]["unresolved"]), 20)
        result = trace_events([event(1, A, C, 101)], checkpoint(), [seed()])
        self.assertEqual(result["status"], "hold")
        self.assertIn("inconsistent_balance", {c["reason"] for c in result["coverage"]})

    def test_unsupported_route_and_hop_limit_never_become_clean(self):
        cp = checkpoint()
        cp["balances"][1]["kind"] = "unsupported_route"
        result = trace_events([event(1, A, B, 50), event(2, B, C, 10)], cp, [seed()])
        self.assertEqual(result["status"], "hold")
        self.assertIn("unsupported_route", {c["reason"] for c in result["coverage"]})
        result = trace_events(
            [event(1, A, C, 50), event(2, C, B, 50)], checkpoint(), [seed()], max_hops=1
        )
        self.assertEqual(rational(account(result, B)["unknown"]), 20)
        self.assertEqual(rational(result["claims"][0]["unresolved"]), 20)
        self.assertIn("hop_limit", {c["reason"] for c in result["coverage"]})

    def test_assets_are_independent_and_burn_is_a_conservation_sink(self):
        cp = checkpoint()
        cp["balances"].extend(
            {"address": a, "asset": OTHER, "amount": str(n), "kind": "wallet"}
            for a, n in ((A, 10), (B, 0))
        )
        result = trace_events(
            [event(1, A, B, 10, OTHER), event(2, A, "0x" + "00" * 20, 50)], cp, [seed()]
        )
        self.assertEqual(rational(account(result, B, OTHER)["tainted"]), 0)
        self.assertEqual(rational(result["claims"][0]["burned"]), 20)
        self.assertEqual(rational(result["claims"][0]["live"]), 20)

    def test_conflicting_seeds_and_bad_sources_are_rejected(self):
        for s in (
            {**seed(), "amount": "101"},
            {**seed(), "source": ""},
            {**seed(), "start_block": 1},
            {**seed(), "amount": "-1"},
        ):
            with self.subTest(seed=s), self.assertRaises(TraceError):
                trace_events([], checkpoint(), [s])
        for s in (
            {**seed(), "amount": "30"},
            {**seed(), "claim_id": "other-ambiguous-claim"},
        ):
            with self.assertRaises(TraceError):
                trace_events([], checkpoint(), [seed(), s])

    def test_malformed_account_kind_is_rejected_as_input_error(self):
        cp = checkpoint()
        cp["balances"][0]["kind"] = []
        with self.assertRaises(TraceError):
            trace_events([], cp, [seed()])

    def test_event_order_duplicates_and_invalid_identity_are_rejected(self):
        first = event(1, A, B, 50)
        for events in (
            [first, first],
            [event(2, B, C, 20), first],
            [{**first, "amount": "-1"}],
            [{**first, "event_id": "wrong-chain-event"}],
        ):
            with self.subTest(events=events), self.assertRaises(TraceError):
                trace_events(events, checkpoint(), [seed()])

    def test_report_is_deterministic_and_does_not_modify_inputs(self):
        cp, seeds, events = checkpoint(), [seed()], [event(1, A, B, 25)]
        before = copy.deepcopy((cp, seeds, events))
        first = trace_events(events, cp, seeds)
        self.assertEqual(first, trace_events(events, cp, seeds))
        self.assertEqual((cp, seeds, events), before)
        self.assertFalse(first["graph"][0]["finalized"])

    def test_two_seed_roots_preserve_quantity_under_randomized_mixing(self):
        for run in range(10):
            rng = random.Random(run)
            balances = {A: 100, B: 100, C: 0}
            events = []
            for n in range(1, 101):
                sender, recipient = rng.sample([A, B, C], 2)
                quantity = rng.randrange(balances[sender] + 1)
                balances[sender] -= quantity
                balances[recipient] += quantity
                events.append(event(n, sender, recipient, quantity))
            second_seed = {**seed(20), "claim_id": "claim-2", "address": B}
            result = trace_events(
                events, checkpoint(), [seed(), second_seed], max_hops=3
            )
            for claim in result["claims"]:
                self.assertEqual(
                    sum(rational(claim[k]) for k in ("live", "burned", "unresolved")),
                    int(claim["seeded"]),
                )
            self.assertEqual(
                {a["address"]: int(a["balance"]) for a in result["accounts"]}, balances
            )

"""Durable request reservations shared by cooperating local controller processes."""
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import time
import argparse

from .credential_broker import BrokerDenied, SharedChatBudget


class PersistentChatBudget(SharedChatBudget):
    """SQLite commit precedes admission; no refunds or automatic database creation.

    Local file ownership is trusted. This does not defend against a controller
    restoring an older database, bypassing the broker, or changing the host clock.
    """

    @classmethod
    def create(cls, path, *, max_calls, output_budget, ttl_seconds):
        SharedChatBudget(max_calls=max_calls, output_budget=output_budget, ttl_seconds=ttl_seconds)
        path = Path(path).absolute()
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        now = time.time()
        with closing(sqlite3.connect(path, timeout=5)) as connection:
            connection.execute("PRAGMA synchronous=FULL")
            with connection:
                connection.execute("CREATE TABLE quota (id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL)")
                connection.execute("INSERT INTO quota VALUES (1, ?)", (json.dumps({
                    "schema_version": "praxile.persistent_chat_budget.v1", "max_calls": max_calls,
                    "output_budget": output_budget, "expires_at": now + ttl_seconds,
                    "last_reserved_at": now, "remaining_calls": max_calls,
                    "remaining_output": output_budget, "roles": {}}),))
        return cls(path)

    def __init__(self, path):
        self._path = Path(path).absolute()
        self.status()

    def _read(self, connection):
        row = connection.execute("SELECT state FROM quota WHERE id=1").fetchone()
        if row is None:
            raise BrokerDenied("quota state missing")
        state = json.loads(row[0])
        fields = {"schema_version", "max_calls", "output_budget", "expires_at", "last_reserved_at",
                  "remaining_calls", "remaining_output", "roles"}
        if (not isinstance(state, dict) or set(state) != fields
                or state["schema_version"] != "praxile.persistent_chat_budget.v1"):
            raise BrokerDenied("invalid quota state")
        for limit, remaining, maximum in (("max_calls", "remaining_calls", 10000),
                                          ("output_budget", "remaining_output", 100_000_000)):
            if (type(state[limit]) is not int or not 1 <= state[limit] <= maximum
                    or type(state[remaining]) is not int or not 0 <= state[remaining] <= state[limit]):
                raise BrokerDenied("invalid quota balance")
        if any(type(state[key]) not in (int, float) or not math.isfinite(state[key]) for key in
               ("expires_at", "last_reserved_at")):
            raise BrokerDenied("invalid quota clock")
        roles = state["roles"]
        if not isinstance(roles, dict):
            raise BrokerDenied("invalid quota roles")
        for role, usage in roles.items():
            if (not isinstance(role, str) or not role or len(role) > 80 or not isinstance(usage, dict)
                    or set(usage) != {"reserved_calls", "reserved_output"}
                    or any(type(value) is not int or value < 0 for value in usage.values())):
                raise BrokerDenied("invalid role reservation")
        if (sum(value["reserved_calls"] for value in roles.values()) != state["max_calls"] - state["remaining_calls"]
                or sum(value["reserved_output"] for value in roles.values()) != state["output_budget"] - state["remaining_output"]):
            raise BrokerDenied("quota conservation mismatch")
        return state

    def _connect(self):
        if self._path.is_symlink() or not self._path.is_file():
            raise BrokerDenied("quota database missing or unsafe")
        connection = sqlite3.connect(self._path.as_uri() + "?mode=rw", uri=True, timeout=5)
        try:
            connection.execute("PRAGMA synchronous=FULL")
        except BaseException:
            connection.close()
            raise
        return connection

    def reserve(self, role, maximum):
        if (not isinstance(role, str) or not role or len(role) > 80
                or type(maximum) is not int or maximum <= 0):
            raise ValueError("invalid shared reservation")
        with closing(self._connect()) as connection:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                state = self._read(connection)
                now = time.time()
                if now < state["last_reserved_at"] or now >= state["expires_at"]:
                    raise BrokerDenied("shared budget expired or clock moved backwards")
                if state["remaining_calls"] < 1 or state["remaining_output"] < maximum:
                    raise BrokerDenied("shared budget exhausted")
                state["remaining_calls"] -= 1
                state["remaining_output"] -= maximum
                state["last_reserved_at"] = now
                usage = state["roles"].setdefault(role, {"reserved_calls": 0, "reserved_output": 0})
                usage["reserved_calls"] += 1
                usage["reserved_output"] += maximum
                connection.execute("UPDATE quota SET state=? WHERE id=1", (json.dumps(state),))

    def status(self):
        with closing(self._connect()) as connection:
            state = self._read(connection)
        now = time.time()
        return {"schema_version": "praxile.shared_chat_budget.v1", "storage": "sqlite",
                "remaining_calls": state["remaining_calls"], "remaining_output": state["remaining_output"],
                "expires_at": state["expires_at"], "expired": now >= state["expires_at"],
                "clock_rollback_detected": now < state["last_reserved_at"], "roles": state["roles"],
                "scope": "local_controller_request_admission_and_output_reservations",
                "total_token_bound_verified": False, "cost_bound_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Create a new quota database; never overwrite")
    create.add_argument("--database", type=Path, required=True)
    create.add_argument("--max-calls", type=int, required=True)
    create.add_argument("--output-budget", type=int, required=True)
    create.add_argument("--ttl-seconds", type=int, required=True)
    show = commands.add_parser("show", help="Read quota state without reserving or extending it")
    show.add_argument("--database", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            budget = PersistentChatBudget.create(args.database, max_calls=args.max_calls,
                output_budget=args.output_budget, ttl_seconds=args.ttl_seconds)
        else:
            budget = PersistentChatBudget(args.database)
        print(json.dumps(budget.status(), indent=2))
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(2, f"Quota operation rejected: {type(error).__name__}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

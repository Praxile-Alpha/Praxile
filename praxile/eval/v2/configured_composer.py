"""Configured model role and durable usage for opt-in CLI composition."""
from contextlib import contextmanager
import hashlib
import re

from ...model import ModelRouter
from ...trajectory import TrajectoryLogger
from ...utils import read_json, write_json
from .manifest import _redact
from .probe_composer import compose_after_development_probes
from .schema import EvalSchemaError, canonical_json


@contextmanager
def configured_design_router(config, *, cycle_id, applies_to, private=False):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}", cycle_id):
        raise EvalSchemaError("unsafe development cycle ID")
    for key in ("repositories", "task_signals", "does_not_apply_when"):
        values = applies_to.get(key)
        if (not isinstance(values, list) or not 1 <= len(values) <= 20
                or any(not isinstance(value, str) or not value.strip() or len(value) > 200 for value in values)):
            raise EvalSchemaError("CLI composition requires explicit bounded repositories, task signals and anti-scope")
    router = ModelRouter(config)
    try:
        router.route("proposal_composer", private=private)
        route = router.describe_route("proposal_composer", private=private)
        # Freeze configuration without storing credentials or raw endpoint settings.
        identity = {"implementation": "praxile.probe_composer.v1", "role": "proposal_composer",
                    "provider": route["provider"], "model": route["model"], "private": private,
                    "config_digest": hashlib.sha256(canonical_json(_redact(config.data)).encode()).hexdigest(),
                    "scope_digest": hashlib.sha256(canonical_json(applies_to).encode()).hexdigest()}
        yield router, identity
    finally:
        for provider in router.providers.values():
            close = getattr(getattr(provider, "transport", None), "close", None)
            if callable(close):
                close()


@contextmanager
def configured_probe_composer(config, *, cycle_id, applies_to, private=False):
    with configured_design_router(config, cycle_id=cycle_id, applies_to=applies_to, private=private) as (router, identity):
        path = config.paths.state / "eval/v2/development_cycles" / cycle_id / "composer-trajectory.json"

        def compose(parents, reports):
            previous = read_json(path, None)
            logger = TrajectoryLogger.from_data(previous) if previous is not None else TrajectoryLogger(
                f"Compose development cycle {cycle_id}", {"purpose": "proposal_composer"})
            try:
                return compose_after_development_probes(parents, reports, router=router, logger=logger,
                    candidate_id=f"{cycle_id}.composed", applies_to=applies_to, private=private)
            finally:
                write_json(path, logger.data)

        yield compose, identity

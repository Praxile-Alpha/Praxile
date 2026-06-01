from __future__ import annotations

from .assets import AssetsStoreMixin
from .base import BaseStore
from .feedback import FeedbackStoreMixin
from .graph import GraphStoreMixin
from .index import IndexStoreMixin
from .proposals import ProposalsStoreMixin
from .retrieval import RetrievalStoreMixin
from .trajectories import TrajectoriesStoreMixin


class ExperienceStore(
    IndexStoreMixin,
    FeedbackStoreMixin,
    GraphStoreMixin,
    TrajectoriesStoreMixin,
    ProposalsStoreMixin,
    RetrievalStoreMixin,
    AssetsStoreMixin,
    BaseStore,
):
    pass

"""Crawl state in SQLite (DESIGN §9).

One module per table, each with its SQL at the top (named after the method that runs it):
`meta`, `matches`, `players`, `tiers` (tier_progress), `league_pages`, `ranks`
(rank_queue + rank_lookups), `parked`. `store.StateStore` opens the database and holds the
operations that change several tables at once. Timestamps are epoch seconds.
"""

from crawler.state.matches import MatchRow
from crawler.state.parked import ParkedItem
from crawler.state.players import Player
from crawler.state.store import StateStore
from crawler.state.tiers import TierProgress

__all__ = ["MatchRow", "ParkedItem", "Player", "StateStore", "TierProgress"]

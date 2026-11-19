"""Spawn-safe isolated battle workers shared by CLI and browser simulations."""
import contextlib
import multiprocessing
import os
import random
import json
from contextvars import ContextVar
from concurrent.futures import ProcessPoolExecutor

_tournament = None
_scope = None
_quiet = None
_pools = ContextVar('simulation_pools', default=None)


@contextlib.contextmanager
def parallel_session():
    pools = {}
    token = _pools.set(pools)
    try:
        yield
    finally:
        for pool in pools.values():
            pool.shutdown(wait=True, cancel_futures=True)
        _pools.reset(token)


def initialize(settings, season):
    global _tournament, _scope, _quiet
    from combat_settings import settings_scope
    _scope = settings_scope(settings)
    _scope.__enter__()
    _quiet = open(os.devnull, 'w', encoding='utf-8')
    # Worker logs are returned as exceptions; children never share a log file.
    import logging
    with contextlib.redirect_stdout(_quiet), contextlib.redirect_stderr(_quiet):
        import ai_battle as ab
        ab.logger.handlers[:] = [logging.NullHandler()]
        ab.logger.setLevel(logging.WARNING)
        _tournament = ab.Tournament()
        _tournament.init_characters()
    _tournament._parallel_prelim = True
    _tournament.char_season_status = season


def battle(task):
    seed, season, args, kwargs = task
    random.seed(seed)
    _tournament.char_season_status = season
    return _tournament.battle(*args, **kwargs)


class Battles:
    def __init__(self, workers, settings, season):
        # Spawned interpreters must agree on set/hash iteration as well as RNG seeds.
        os.environ.setdefault('PYTHONHASHSEED', '0')
        self.workers = max(1, min(workers, os.cpu_count() or 1))
        self.season = season
        pools = _pools.get()
        self.shared = pools is not None
        key = (self.workers, json.dumps(settings, sort_keys=True))
        self.pool = pools.get(key) if self.shared else None
        if self.pool is None:
            self.pool = ProcessPoolExecutor(max_workers=self.workers,
                mp_context=multiprocessing.get_context('spawn'),
                initializer=initialize, initargs=(settings, season))
            if self.shared:
                pools[key] = self.pool

    def __enter__(self):
        return self

    def submit(self, seed, *args, **kwargs):
        return self.pool.submit(battle, (seed, self.season, args, kwargs))

    def __exit__(self, *exc):
        if exc[0] is not None and hasattr(self.pool, 'terminate_workers'):
            self.pool.terminate_workers()
        elif not self.shared:
            self.pool.shutdown(wait=True, cancel_futures=True)

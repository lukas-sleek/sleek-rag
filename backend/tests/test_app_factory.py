"""Plan 19.0 T8b cache + concurrency tests."""
from __future__ import annotations

import asyncio

import pytest

from app.adk import app_factory


@pytest.fixture(autouse=True)
def _stub_vertex_init(monkeypatch):
    """AdkApp construction calls vertexai.init under the hood — stub it
    out so the cache tests don't need real GCP credentials."""
    monkeypatch.setattr(app_factory, "_init_vertex", lambda: None)
    yield


@pytest.fixture(autouse=True)
def _reset_cache():
    app_factory._apps.clear()
    app_factory._locks.clear()
    yield
    app_factory._apps.clear()
    app_factory._locks.clear()


class _FakeApp:
    def __init__(self, corpus_name):
        self.corpus_name = corpus_name
        self._set_up = False

    def set_up(self):
        self._set_up = True


@pytest.mark.asyncio
async def test_cache_hit_returns_same_instance(monkeypatch):
    monkeypatch.setattr(
        app_factory, "AdkApp", lambda *, agent: _FakeApp("c1")
    )
    a = await app_factory.get_or_build_app("c1")
    b = await app_factory.get_or_build_app("c1")
    assert a is b
    assert a._set_up is True


@pytest.mark.asyncio
async def test_eviction_at_max(monkeypatch):
    counter = {"n": 0}

    def make_app(*, agent):
        counter["n"] += 1
        return _FakeApp(f"c{counter['n']}")

    monkeypatch.setattr(app_factory, "AdkApp", make_app)
    monkeypatch.setattr(app_factory, "_MAX_APPS", 3)

    a1 = await app_factory.get_or_build_app("c1")
    await app_factory.get_or_build_app("c2")
    await app_factory.get_or_build_app("c3")
    await app_factory.get_or_build_app("c4")  # evicts c1

    assert "c1" not in app_factory._apps
    assert "c4" in app_factory._apps
    a1_again = await app_factory.get_or_build_app("c1")
    assert a1_again is not a1  # rebuilt


@pytest.mark.asyncio
async def test_concurrent_get_or_build_dedupes(monkeypatch):
    builds = {"n": 0}

    def make_app(*, agent):
        builds["n"] += 1
        return _FakeApp("c1")

    monkeypatch.setattr(app_factory, "AdkApp", make_app)

    apps = await asyncio.gather(
        app_factory.get_or_build_app("c1"),
        app_factory.get_or_build_app("c1"),
        app_factory.get_or_build_app("c1"),
    )
    assert builds["n"] == 1
    assert apps[0] is apps[1] is apps[2]

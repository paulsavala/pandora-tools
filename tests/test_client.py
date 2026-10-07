"""Client tests against a fake PGN API (no network needed)."""
from pathlib import Path

import pytest

import pandora_tools as pdt
from pandora_tools import cache

DATA = Path(__file__).parent / "data"
SAMPLE = (DATA / "Pandora63s1_LaPorteTX_L2_rnvh3p1-8_gg5WZXpKyH4kCjvdYRAN.txt").read_bytes()

LOCATIONS = [
    {"name": "AustinTX", "long_name": "St. Edward's University", "lat": 30.23, "lon": -97.76, "alt": 180,
     "aliases": ["AustinTX"]},
    {"name": "AldineTX", "long_name": "University Of Houston Trailer", "lat": 29.901, "lon": -95.326,
     "alt": 24, "aliases": ["AldineTX"]},
    {"name": "Innsbruck", "long_name": "Innsbruck", "lat": 47.26, "lon": 11.38, "alt": 616,
     "aliases": ["Innsbruck", "InnsbruckOld"]},
]


def file_rec(day, code, modified="2026-09-01T00:00:00Z"):
    return {"filename": f"Pandora257s1_AustinTX_{day}_L2_{code}c2d20230110p1-8.txt", "size": 173,
            "created_time": modified, "modified_time": modified, "metadata_code": code,
            "metadata_blickp_version": "p1-8", "metadata_spectrometer": "1", "metadata_date": day,
            "metadata_cf_version": "2", "metadata_cf_date": "20230110"}


class FakeResponse:
    def __init__(self, payload=None, content=b"", status=200):
        self._payload, self.content, self.status_code = payload, content, status
        self.text, self.url = "", "fake"

    def json(self):
        return self._payload


class FakeAPI:
    def __init__(self):
        self.calls = []

    def __call__(self, path, params=None):
        self.calls.append((path, params))
        if path == "files/locations":
            return FakeResponse(LOCATIONS)
        if path == "files/AustinTX":
            return FakeResponse([{"pan_id": 257}])
        if path == "files/AustinTX/257":
            return FakeResponse([{"spectrometer": 1}])
        if path == "files/AustinTX/257/1":
            return FakeResponse([{"level": "L1"}, {"level": "L2"}])
        if path == "files/AustinTX/257/1/L2":
            recs = [file_rec("20260828", "rnvh3"), file_rec("20260829", "rnvh3"), file_rec("20260830", "rnvh3"),
                    file_rec("20260828", "rfuh5"), file_rec("20260829", "rfuh5"),
                    # a reissued file for the same day: only the newest should be kept
                    {**file_rec("20260828", "rfuh5", "2026-09-05T00:00:00Z"),
                     "filename": "Pandora257s1_AustinTX_20260828_L2_rfuh5c3d20240101p1-8.txt"}]
            code = (params or {}).get("code")
            return FakeResponse([r for r in recs if code in (None, r["metadata_code"])])
        if path.startswith("download/"):
            return FakeResponse(content=SAMPLE)
        raise AssertionError(f"unexpected path {path}")


@pytest.fixture
def client(tmp_path):
    c = pdt.PandoraClient(cache_dir=tmp_path, verbose=False)
    c._get = FakeAPI()
    return c


def test_browse(client):
    assert client.instruments("austintx") == [257]
    assert client.spectrometers("AustinTX", 257) == [1]
    assert client.levels("AustinTX", 257) == ["L1", "L2"]
    assert client.resolve_location("InnsbruckOld") == "Innsbruck"
    with pytest.raises(LookupError):
        client.resolve_location("Atlantis")


def test_find_locations(client):
    near = client.find_locations(near=(30.27, -97.74), radius_km=300)
    assert list(near["name"]) == ["AustinTX", "AldineTX"]
    assert list(client.find_locations("houston")["name"]) == ["AldineTX"]


def test_list_files_dates_inclusive_and_latest_only(client):
    f = client.list_files("AustinTX", 257, "2026-08-28", "2026-08-29")
    assert set(f["date"].astype(str)) == {"2026-08-28", "2026-08-29"}       # 30th excluded
    hcho28 = f[(f["product"] == "rfuh5") & (f["date"].astype(str) == "2026-08-28")]
    assert len(hcho28) == 1 and "c3d2024" in hcho28["filename"].iloc[0]
    params = client._get.calls[-1][1]
    assert params["start"] == "2026-08-28T00:00:00Z" and params["end"] == "2026-08-29T23:59:59Z"


def test_products_and_catalog(client):
    p = client.products("AustinTX", 257, "2026-08-28", "2026-08-30")
    assert dict(zip(p["product"], p["n_days"])) == {"rfuh5": 2, "rnvh3": 3}
    cat = client.catalog("AustinTX", "2026-08-28", "2026-08-30")
    assert set(cat["product"]) == {"rfuh5", "rnvh3"}


def test_download_uses_cache(client, tmp_path):
    files = client.list_files("AustinTX", 257, "2026-08-28", product="rnvh3")
    paths = client.download(files)
    assert paths[0].exists() and paths[0].parent == tmp_path / "L2" / "AustinTX"
    n = sum(1 for c in client._get.calls if c[0].startswith("download/"))
    client.download(files)
    assert sum(1 for c in client._get.calls if c[0].startswith("download/")) == n   # cached
    client.download(files, refresh=True)
    assert sum(1 for c in client._get.calls if c[0].startswith("download/")) == n + 1
    assert not list(tmp_path.rglob("*.part"))


def test_fetch(client):
    no2, hcho = client.fetch("AustinTX", 257, "2026-08-28", "2026-08-29", products=["rnvh3", "rfuh5"])
    assert isinstance(no2, pdt.PandoraFrame) and len(no2.headers) == 2
    single = client.fetch("AustinTX", 257, "2026-08-28", products="rnvh3")
    assert isinstance(single, pdt.PandoraFrame)
    with pytest.raises(LookupError, match="available"):
        client.fetch("AustinTX", 257, "2026-08-28", products="rout2")


def test_bad_download_is_rejected(client):
    def bad(path, params=None):
        return FakeResponse(content=b'{"detail": "error"}')
    client._get = bad
    with pytest.raises(RuntimeError):
        client.download("Pandora257s1_AustinTX_20260828_L2_rnvh3c2d20230110p1-8.txt")


def test_cache_dir_resolution(tmp_path, monkeypatch):
    monkeypatch.delenv(cache.ENV_VAR, raising=False)
    assert cache.resolve_cache_dir(tmp_path) == (tmp_path, "cache_dir argument")
    monkeypatch.setenv(cache.ENV_VAR, str(tmp_path / "env"))
    assert cache.resolve_cache_dir()[1].startswith(cache.ENV_VAR)
    monkeypatch.delenv(cache.ENV_VAR)
    shared = tmp_path / "Shareddrives" / "Atmos Group" / "pandora_cache"
    shared.mkdir(parents=True)
    monkeypatch.setattr(cache, "SHARED_DRIVES_ROOT", str(tmp_path / "Shareddrives"))
    assert cache.resolve_cache_dir() == (shared, "shared drive")


def test_dates_fallback_and_formats(client):
    real = client._get

    def api(path, params=None):
        r = real(path, params)
        if path.endswith("/L2"):
            recs = r.json()
            recs[0] = {**recs[0], "metadata_date": None}          # fall back to filename
            recs[1] = {**recs[1], "metadata_date": "2026-08-29"}  # ISO format
            return FakeResponse(recs)
        return r
    client._get = api
    f = client.list_files("AustinTX", 257, "2026-08-28", "2026-08-30", product="rnvh3")
    assert len(f) == 3


def test_tz_aware_dates_use_utc():
    import pandas as pd
    from pandora_tools.client import _as_date
    assert str(_as_date(pd.Timestamp("2026-08-29 21:00", tz="America/Chicago"))) == "2026-08-30"

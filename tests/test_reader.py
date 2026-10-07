from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import pandora_tools as pdt

DATA = Path(__file__).parent / "data"
HOUSTON = DATA / "Pandora25s1_HoustonTX_L2_rfuh5p1-8_ctg8THtVaS1Mpy1K6Gjd.txt"      # HCHO sky
LAPORTE_SUN = DATA / "Pandora58s1_LaPorteTX_L2_rfus5p1-8_sGOkU9ISfS8gQBcohIik.txt"  # HCHO sun
ALDINE = DATA / "Pandora61s1_AldineTX_L2_rnvh3p1-8_vCDSGPOKd4wdu4SYjI9a.txt"        # NO2+H2O sky
LAPORTE = DATA / "Pandora63s1_LaPorteTX_L2_rnvh3p1-8_gg5WZXpKyH4kCjvdYRAN.txt"      # NO2+H2O sky


def raw_rows(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    seps = [i for i, l in enumerate(lines) if l.startswith("-----")]
    return [l.split() for l in lines[seps[1] + 1:] if l.strip()]


@pytest.mark.parametrize("path, n_rows", [(HOUSTON, 1428), (LAPORTE_SUN, 406), (ALDINE, 789), (LAPORTE, 162)])
def test_reads_every_row(path, n_rows):
    df = pdt.read(path)
    assert isinstance(df, pdt.PandoraFrame)
    assert len(df) == n_rows
    assert df["utc_time"].notna().all()
    assert str(df["utc_time"].dt.tz) == "UTC"


@pytest.mark.parametrize(
    "path, name, column_number",
    [
        # the column numbers from the files' own headers
        (HOUSTON, "hcho_qa", 42), (HOUSTON, "hcho_surf_conc", 45), (HOUSTON, "hcho_trop_col", 49),
        (ALDINE, "no2_qa", 53), (ALDINE, "no2_surf_conc", 56), (ALDINE, "no2_trop_col", 62),
        (ALDINE, "h2o_trop_col", 49), (LAPORTE_SUN, "hcho_total_col", 39), (LAPORTE_SUN, "rms", 8),
        (HOUSTON, "rms", 10),
    ],
)
def test_names_point_at_the_right_columns(path, name, column_number):
    df = pdt.read(path, mask_missing=False)
    assert df.column_info(name)["column_number"] == column_number
    raw = pd.to_numeric(pd.Series([r[column_number - 1] for r in raw_rows(path)]))
    np.testing.assert_allclose(df[name].to_numpy(dtype=float), raw.to_numpy(dtype=float))


def test_same_quantity_same_name_across_products():
    # rms is column 10 in sky files but column 8 in the direct-sun file
    assert "rms" in pdt.read(HOUSTON).columns and "rms" in pdt.read(LAPORTE_SUN).columns


def test_missing_codes_masked_only_where_documented():
    raw = pdt.read(ALDINE, mask_missing=False)
    df = pdt.read(ALDINE)
    assert (raw["resolution_change"] == -999).all()
    assert df["resolution_change"].isna().all()
    assert (raw["no2_surf_conc"] < -1e98).sum() == 1
    assert df["no2_surf_conc"].isna().sum() == 1
    # real zeros / angles are untouched
    pd.testing.assert_series_equal(raw["saa"], df["saa"])


def test_ragged_rows_go_to_profiles():
    df = pdt.read(HOUSTON)
    rows = raw_rows(HOUSTON)
    n_fixed = 52                       # layer 1 starts at column 53
    expected = sum((len(r) - n_fixed) // 2 for r in rows)
    assert len(df.profiles) == expected
    p = df.profiles
    assert set(p["species"]) == {"hcho"}
    first = p[p["utc_time"] == p["utc_time"].iloc[0]]
    assert first["layer"].tolist() == list(range(1, len(first) + 1))
    assert first["bottom_height"].iloc[0] == 0
    assert (first["bottom_height"].iloc[1:].to_numpy() == first["top_height"].iloc[:-1].to_numpy()).all()


def test_two_species_profiles():
    p = pdt.read(ALDINE).profiles
    assert set(p["species"]) == {"h2o", "no2"}


def test_direct_sun_has_no_profiles():
    assert pdt.read(LAPORTE_SUN).profiles is None


def test_metadata_and_station_columns():
    df = pdt.read(ALDINE)
    assert df.location == "AldineTX"
    assert df.meta["Instrument number"] == "61"
    assert df["product"].iloc[0] == "rnvh3"
    assert df["instrument"].iloc[0] == 61
    assert df["latitude"].iloc[0] == pytest.approx(29.9011, abs=1e-3)


def test_metadata_survives_slicing():
    df = pdt.read(ALDINE)
    sub = df[df["sza"] < 60][["utc_time", "no2_trop_col", "no2_qa", "location", "instrument",
                             "spectrometer", "product"]].head(20).copy()
    assert isinstance(sub, pdt.PandoraFrame)
    assert sub.meta["DOI"] == df.meta["DOI"]
    assert set(sub.profiles["utc_time"]) <= set(sub["utc_time"])


def test_filter_quality():
    df = pdt.read(ALDINE)
    q = df.filter_quality("no2")
    assert set(q["no2_qa"]) <= {0, 1, 10, 11}
    assert len(q) == df["no2_qa"].isin([0, 1, 10, 11]).sum()
    assert len(df.filter_quality("nitrogen dioxide")) == len(q)
    assert set(df.filter_quality("no2", keep=["high"], assured_only=True)["no2_qa"]) <= {0}
    with pytest.raises(ValueError):
        df.filter_quality()          # two species, must choose
    assert len(pdt.read(HOUSTON).filter_quality()) > 0   # single species: no need to say


def test_find_and_column_info():
    df = pdt.read(HOUSTON)
    found = df.find_columns("tropospheric")
    assert "hcho_trop_col" in set(found["name"])
    assert df.column_info("hcho_trop_col")["units"] == "moles per square meter"
    with pytest.raises(KeyError):
        df.column_info("nope")


def test_decode_bits():
    df = pdt.read(ALDINE)
    reasons = df.decode_bits("no2_dq1_bits")
    i = df.index[df["no2_dq1_bits"] == 1][0]
    assert reasons[i] == ["L2Fit data quality above 0"]


def test_concat_keeps_metadata():
    both = pdt.read([ALDINE, LAPORTE])
    assert sorted(both.location) == ["AldineTX", "LaPorteTX"]
    assert len(both.headers) == 2
    assert isinstance(both.meta["Short location name"], dict)
    assert len(both.stations) == 2
    assert both["utc_time"].is_monotonic_increasing


def test_plain_pd_concat_gives_clear_error():
    df = pdt.read(ALDINE)
    x = pd.concat([df, df])
    with pytest.raises(AttributeError, match="pandora_tools.concat"):
        x.meta


def test_glob():
    assert len(pdt.read(str(DATA / "*rnvh3*.txt")).headers) == 2


def test_merge_products_exact_and_tolerance(capsys):
    no2 = pdt.read(LAPORTE)
    # fake an HCHO product from the same instrument by renaming species columns
    h = pdt.read(LAPORTE)
    h = h.rename(columns={"no2_trop_col": "hcho_trop_col"})
    h["product"] = "rfuh5"
    h = h[["utc_time", "location", "instrument", "spectrometer", "product", "hcho_trop_col", "sza"]]
    m = pdt.merge_products(no2, h)
    assert len(m) == len(no2)
    assert {"sza_rnvh3", "sza_rfuh5", "no2_trop_col", "hcho_trop_col"} <= set(m.columns)
    assert isinstance(m, pdt.PandoraFrame) and m.meta
    # shift times by 30 s: exact match finds nothing, tolerance finds all
    h2 = h.copy()
    h2["utc_time"] = h2["utc_time"] + pd.Timedelta("30s")
    assert len(pdt.merge_products(no2, h2, report=False)) == 0
    assert len(pdt.merge_products(no2, h2, tolerance="1min", report=False)) == len(no2)
    assert "rows" in capsys.readouterr().out


def test_local_time():
    df = pdt.read(ALDINE, tz="America/Chicago")
    assert (df["local_time"] - df["utc_time"]).abs().max() == pd.Timedelta(0)  # same instant
    assert str(df["local_time"].dt.tz) == "America/Chicago"
    assert "local_time" not in pdt.read(ALDINE, tz=None).columns


# ---- regression tests from code review ------------------------------------

def _strip_fractions(src, dst):
    text = src.read_text(encoding="utf-8")
    import re
    head, sep, body = text.rpartition("-" * 87 + "\n")
    body = re.sub(r"^(\d{8}T\d{6})\.\dZ", r"\1Z", body, flags=re.M)
    dst.write_text(head + sep + body, encoding="utf-8")
    return dst


def test_time_resolution_consistent(tmp_path):
    a = pdt.read(LAPORTE)
    b = pdt.read(_strip_fractions(LAPORTE, tmp_path / "Pandora63s1_LaPorteTX_L2_rnvh3p1-8_x.txt"))
    assert a["utc_time"].dtype == b["utc_time"].dtype
    b = b.rename(columns={"no2_trop_col": "hcho_trop_col"})[
        ["utc_time", "location", "instrument", "spectrometer", "product", "hcho_trop_col"]]
    b["product"] = "rfuh5"
    m = pdt.merge_products(a, b, tolerance="1s", report=False)
    assert len(m) == len(a)
    assert m["product"].iloc[0] == "rnvh3+rfuh5"


def test_profiles_follow_index_and_column_subsets():
    df = pdt.read(LAPORTE)
    three = df.iloc[:3]
    n = len(three.profiles)
    assert 0 < n < len(df.profiles)
    assert len(df.set_index("utc_time").iloc[:3].profiles) == n
    assert len(df[["utc_time", "no2_trop_col", "location"]].iloc[:3].profiles) == n


def test_utc_time_in_catalog():
    df = pdt.read(HOUSTON)
    assert df.column_info("utc_time")["column_number"] == 1
    assert set(df.columns_info["name"]) - {c for c in df.columns_info["name"] if c.endswith(("_layer_top", "_layer_col"))} <= set(df.columns)


def test_concat_keep_index():
    a, b = pdt.read(ALDINE), pdt.read(LAPORTE)
    assert "index" not in pdt.concat([a, b], ignore_index=False).columns


def test_categorical_not_retrieved_code_masked():
    raw = pdt.read(ALDINE, mask_missing=False)
    df = pdt.read(ALDINE)
    assert (raw["h2o_heterogeneity"] == -6).any()
    assert not (df["h2o_heterogeneity"] == -6).any()


def test_bracket_in_folder_name(tmp_path):
    d = tmp_path / "drive [2026]"
    d.mkdir()
    p = d / LAPORTE.name
    p.write_bytes(LAPORTE.read_bytes())
    assert len(pdt.read(str(p))) == 162


def test_empty_data_section(tmp_path):
    text = LAPORTE.read_text(encoding="utf-8")
    head, sep, _ = text.rpartition("-" * 87 + "\n")
    p = tmp_path / LAPORTE.name
    p.write_text(head + sep, encoding="utf-8")
    df = pdt.read(p)
    assert len(df) == 0 and df.profiles is None

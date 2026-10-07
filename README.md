# pandora-tools

Read, browse and download NASA / Pandonia Global Network (PGN) Pandora L2 data
as pandas DataFrames, without looking up column numbers.

```python
import pandora_tools as pdt

client = pdt.PandoraClient()
no2, hcho = client.fetch("AustinTX", 257, "2026-08-28", products=["rnvh3", "rfuh5"])

no2 = no2.filter_quality("no2")                  # flags 0, 1, 10, 11
no2[["local_time", "no2_surf_conc", "no2_trop_col"]].head()
```

## Install

Every notebook starts with one cell (Colab starts a fresh machine each session):

```python
!pip install -q git+https://github.com/paulsavala/pandora-tools.git
```

On a laptop, install once into your environment (`pip install -q git+https://github.com/paulsavala/pandora-tools.git`),
then just `import pandora_tools` in any notebook.

## Finding data

```python
client.find_locations("TX")                              # name search
client.find_locations(near=(30.27, -97.74), radius_km=300)
client.instruments("AustinTX")                           # [257]
client.products("AustinTX", 257, "2026-08-01", "2026-08-31")
client.catalog(["AustinTX", "HoustonTX"], "2026-08-01", "2026-08-31")
client.list_files("AustinTX", 257, "2026-08-28", product="rnvh3")
```

Dates are **UTC days** and `end` is inclusive. A local (CDT) day spans two UTC
files, so for a full local day fetch that day and the next and filter on `local_time`.

## Reading local files

```python
df = pdt.read("Pandora61s1_AldineTX_L2_rnvh3p1-8.txt")
df = pdt.read("data/*rnvh3*.txt")                        # several files, stacked
```

## What you get: `PandoraFrame`

A normal pandas DataFrame (everything pandas works) with:

| | |
|---|---|
| columns | short, stable names such as `no2_trop_col`, `hcho_surf_conc`, `sza`, `no2_qa`, the same in every file whatever the column number |
| `utc_time`, `local_time` | parsed datetimes |
| station columns | `location`, `instrument`, `spectrometer`, `product`, `latitude`, `longitude`, `altitude`, `source_file` on every row |
| `df.location` | site name (`df["location"]` is still the per-row column) |
| `df.stations` | one row per station/product with the time span |
| `df.meta` / `df.headers` | the file header (`key: value` lines) |
| `df.columns_info` | units, description, kind, codes, original column number |
| `df.column_info("no2_trop_col")` | everything about one column |
| `df.find_columns("tropospheric")` | search columns by text |
| `df.profiles` | layer profiles in long format (species, layer, bottom/top height, partial column) for the rows in `df` |
| `df.filter_quality("no2", keep=("high", "medium"), assured_only=False)` | quality filtering |
| `df.decode_bits("no2_dq1_bits")` | why a row got its quality flag |

**Missing values**: each column's documented missing codes (e.g. `-9e99 = retrieval
not successful`, `-999 = no resolution change fitting`) become NaN. Only negative
codes and ±999-style sentinels in measurement columns are masked; codes like
`0 = north` are left alone. `read(..., mask_missing=False)` or
`PandoraClient(mask_missing=False)` gives raw values.

**Profiles**: rows in profile products have a variable number of layers. The
fixed columns go in the main table; layers go to `df.profiles`.

## Combining frames

Slicing and filtering keep the metadata. `pd.concat` / `pd.merge` drop it (the
data stay correct, since station info is in the columns), so use:

```python
both = pdt.concat([aldine, laporte])                     # several days/sites
ratio = pdt.merge_products(hcho, no2)                    # line up products by time
ratio["fnr"] = ratio["hcho_trop_col"] / ratio["no2_trop_col"]
pdt.merge_products(hcho, no2, tolerance="2min")          # nearest match, if times drift
```

`merge_products` reports how many rows matched. Columns both products have (geometry,
fit residuals, L1 flags) get a product suffix (`sza_rfuh5`, `sza_rnvh3`).

## Where downloaded files go

Downloads can always be fetched again, so the folder is just a cache. It's chosen
automatically (first match wins); `client` prints which one it picked:

1. `PandoraClient(cache_dir=...)`
2. the `PANDORA_DATA_DIR` environment variable
3. **a shared drive that has a `pandora_cache` folder**:
   `/content/drive/Shareddrives/<any drive>/pandora_cache`. Create that folder once
   in the group's shared drive and everyone with Drive mounted shares one cache,
   whatever their own Drive layout.
4. `/content/pandora_cache` on Colab (cleared when the session ends)
5. `~/.cache/pandora_tools` on a laptop

Files are written via a temporary file and a rename, so two people downloading
the same day at once can't corrupt the cache. `refresh=True` re-downloads.

## Tests

```
pip install -e ".[test]"
pytest
```

Tests use four sample files in `tests/data` and a fake API (no network needed).

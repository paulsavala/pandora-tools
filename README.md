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

**New to Python or to this data?** Open
[`examples/getting_started.ipynb`](examples/getting_started.ipynb) in Google Colab
(File → Upload notebook) and run it top to bottom. It downloads, plots and saves
one day of data, and you only edit one settings cell.

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

## Product descriptions

`client.products(...)`, `client.catalog(...)` and `pdt.KNOWN_PRODUCTS` show a
plain-language description next to each product code (`rnvh3` → "Nitrogen
dioxide + water vapor, sky scans ..."). These come from
[`pandora_tools/data/products.csv`](pandora_tools/data/products.csv), a
two-column table you can edit without touching any Python:

```
product,description
rnvh3,"Nitrogen dioxide + water vapor, sky scans (surface conc., tropospheric column, profile)"
rfus5,"Formaldehyde, direct sun (total column)"
```

- Open it in Excel, Google Sheets or any text editor and add a row; save as CSV
  (UTF-8). Keep the header line `product,description` as it is.
- Product codes are lowercase letters then digits (`rnvh3`), one row per code.
- In a text editor, put a description that contains commas in double quotes.
- Products missing from the table still download and read fine; they just show
  an empty description.
- Run `pytest` (or open a PR and let a maintainer run it): it checks the file and
  says which line to fix if something is off.

## Tests

```
pip install -e ".[test]"
pytest
```

Tests use four sample files in `tests/data` and a fake API (no network needed).

## Contributing

### 1. Set up a local copy

```bash
git clone https://github.com/paulsavala/pandora-tools.git
cd pandora-tools
python -m venv .venv && source .venv/bin/activate     # or conda, if you prefer
pip install -e ".[test]"
pytest                                                # everything should pass before you change anything
```

`-e` (editable) means your edits take effect without reinstalling.

### 2. If you use a coding assistant (Claude Code, Codex, Cursor, ...)

The repo has a guide written for coding agents: **`AGENTS.md`**. It covers the
layout, the rules that must not break (e.g. short column names are public and
never renamed), style, and how to test. `CLAUDE.md` simply imports it.

- **Start the assistant from the repo root** (the folder with `pyproject.toml`).
  Claude Code reads `CLAUDE.md` automatically; Codex and many others read
  `AGENTS.md`. If yours reads neither, make your first message
  *"Read AGENTS.md before doing anything."*
- **Ask it to orient first, not code first.** A good opening prompt:
  *"Read AGENTS.md and README.md, run the tests, and summarize how a file goes
  from raw text to a PandoraFrame. Don't change anything yet."*
- **Work on a branch**, one focused change at a time, and have the assistant
  run `pytest` before it says it's done. Bug fixes and new column names should
  come with a test (AGENTS.md explains how).
- **The live API usually isn't reachable from an assistant's sandbox.** For
  changes to `client.py`, ask the assistant for a short snippet, run it yourself
  in Colab, and paste the result back.
- **Review the diff yourself** before committing. Watch especially for renamed
  columns, new dependencies, and changes to what gets turned into NaN.
- When a change alters how things work (a new rule, a new module), ask the
  assistant to update `AGENTS.md` and this README in the same PR.

### 3. Submit

Push your branch and open a pull request against `main` describing what changed
and how you tested it. Releases are tagged (`v0.1.0`, ...) so notebooks that pin
a version keep working.

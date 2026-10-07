# pandora-tools: guide for contributors and coding agents

Python package for reading, browsing and downloading NASA / Pandonia Global
Network (PGN) Pandora L2 data as pandas DataFrames. The users are atmospheric
researchers working in Google Colab notebooks who know pandas well and do not
want to think about file formats. Every change should make their notebooks
shorter or safer, never more surprising.

## Layout

| Path | Role |
|---|---|
| `pandora_tools/data/products.csv` | Product code → plain-language description (`KNOWN_PRODUCTS`). Edited by non-developers; keep it a plain 2-column CSV. |
| `pandora_tools/parser.py` | Raw file → tables. Header parsing, column naming (`_NAME_RULES`), missing-value codes, ragged rows → profiles. No pandas subclass, no network. |
| `pandora_tools/frame.py` | `PandoraFrame` (DataFrame subclass) and its methods; `concat`, `merge_products`. |
| `pandora_tools/reader.py` | `read()` / `read_bytes()`: wraps parser output into a `PandoraFrame`. |
| `pandora_tools/client.py` | `PandoraClient`: PGN API browsing, file listing, download, `fetch()`. |
| `pandora_tools/cache.py` | Download-cache location rules and atomic writes. |
| `tests/` | pytest. `tests/data/` holds four real PGN L2 files (three products). |
| `examples/quickstart.ipynb` | The canonical end-to-end workflow; keep it working. |

Keep this layering: `parser` knows nothing about the API or the subclass;
`client` uses `reader`, never re-implements parsing.

## The PGN L2 format (what the parser relies on)

- `key: value` header, dashed line, `Column N: description [units], code=meaning, ...`
  lines, dashed line, whitespace-delimited rows.
- **Column numbers differ between products and versions.** Columns are
  identified by their description text, never by position. Never hard-code a
  column index anywhere.
- Profile products end each row with a variable number of layers
  (`From Column N: ... (k columns per layer)`). Fixed columns go to the main
  table; layers go to the long-format `profiles` table.
- Product codes (`rnvh3`, `rfuh5`, `rfus5`, ...) determine the column set.
  Sky-scan (`...h`) and direct-sun (`...s`) files have different columns.
- Times are UTC. Daily files are UTC days.

## Invariants: do not break these

1. **Short column names are a public API.** Researchers' notebooks use names
   like `no2_trop_col` and `hcho_qa`. Never rename an existing one. Add new
   names by adding a rule to `_NAME_RULES` (more specific patterns first).
   Unmatched labels fall back to `slugify`; that is fine, don't force names.
2. **Missing-value masking is conservative.** Only codes documented in the
   column's own description are masked, and only negative or |code| ≥ 999 codes
   in numeric columns (negative codes in categorical columns). Codes such as
   `0=north` are real values. `mask_missing=False` must always return raw values.
3. **Metadata survives normal pandas use.** Slicing, boolean filtering,
   `.copy()`, `.query()` must return a `PandoraFrame` with metadata. Anything in
   `PandoraFrame._metadata` needs a class-level default. Operations that drop
   metadata (pd.concat/merge) must fail with a clear message pointing to
   `pandora_tools.concat` / `merge_products`, not with an obscure error.
4. **Station info lives in columns too** (`location`, `instrument`,
   `spectrometer`, `product`, `latitude`, ...), so data stay correct even when
   metadata is lost.
5. **Works on pandas 2.x (Colab) and 3.x.** Watch for: copy-on-write (arrays
   from `.to_numpy()` may be read-only; copy before writing), datetime
   resolution (normalize to `datetime64[ns, UTC]`), default string dtype.
6. **Dependencies: pandas, numpy, requests only.** Ask before adding any other.
7. **Library code doesn't print**, except short progress lines guarded by
   `verbose` in the client and the match summary in `merge_products(report=True)`.
   Use `warnings.warn` for anything a user should notice.
8. The download cache is shared between users: always write via
   `cache.atomic_write`.

## Style

- Python ≥ 3.9. `from __future__ import annotations`; type hints on public functions.
- NumPy-style docstrings on every public function/method, with a short usage
  example when the call isn't obvious. Write docstrings for a scientist, not a
  software engineer: say what comes back and in what units/time zone.
- Error messages say what to do next (e.g. list available products, suggest
  `tolerance=`). Prefer `LookupError`/`ValueError` with a specific message over
  silent empty results.
- Names: `snake_case`; species abbreviations from `parser.SPECIES`
  (`no2`, `hcho`, `h2o`, `o3`, ...); suffixes `_col`, `_conc`, `_unc`, `_qa`,
  `_bits`.
- Small, readable functions over clever ones. No new abstractions without a
  second real use.
- Keep README tables and examples in sync with any API change.

## Testing

```bash
pip install -e ".[test]"
pytest
```

- Every bug fix gets a regression test; every new column rule gets a test that
  checks the name against the raw file's `Column N` value.
- Tests must not use the network. The client is tested by replacing
  `client._get` with a fake (see `tests/test_client.py`).
- Before finishing, run the suite on pandas 2.2 *and* pandas 3 if the change
  touches dtypes, datetimes or the subclass.
- The live API cannot be tested in CI. If you change `client.py`, say so in
  the PR and include a short Colab snippet a human can run to check it.

## PGN API notes

Base `https://api.pandonia-global-network.org/v1`, spec at `/openapi.json`.
Browsing: `/files/locations` → `/files/{location}` (instruments, `pan_id`) →
`/files/{loc}/{inst}` (spectrometers) → `/files/{loc}/{inst}/{spec}` (levels) →
`/files/{loc}/{inst}/{spec}/{level}?start=&end=&code=` (file list; records have
`filename`, `metadata_code`, `metadata_date`, `modified_time`, ...).
Download: `/download/{filename}`. The `end` parameter is inclusive of that
day's file; `list_files` filters to the requested UTC dates itself.

## Git

- Branch from `main`; small focused PRs with a clear description.
- Bump `version` in `pyproject.toml` and tag (`vX.Y.Z`) for releases; notebooks
  pin tags, so tagged releases must stay installable.
- Don't commit build artifacts (`*.egg-info`, `build/`) or downloaded data
  beyond the small samples in `tests/data`.

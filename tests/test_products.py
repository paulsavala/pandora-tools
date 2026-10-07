"""The product descriptions table, pandora_tools/data/products.csv."""
import pytest

import pandora_tools as pdt
from pandora_tools.parser import load_known_products, station_info


def test_shipped_file_loads():
    # Also guards edits to the CSV: a malformed file fails here, not at import in a notebook.
    products = load_known_products()
    assert products == pdt.KNOWN_PRODUCTS
    assert products["rnvh3"].startswith("Nitrogen dioxide + water vapor, sky scans")
    assert {"rnvh3", "rfuh5", "rfus5", "rnvs3", "rout2"} <= set(products)


def test_station_info_uses_table():
    info = station_info({"Data file version": "rfus5p1-8"}, "x.txt")
    assert info["product_description"] == "Formaldehyde, direct sun (total column)"


def write(tmp_path, text, encoding="utf-8"):
    p = tmp_path / "products.csv"
    p.write_text(text, encoding=encoding)
    return p


def test_excel_bom_blank_lines_and_spaces(tmp_path):
    p = write(tmp_path, "product,description\n\n rnvh3 , NO2 sky \n,\n", encoding="utf-8-sig")
    assert load_known_products(p) == {"rnvh3": "NO2 sky"}


@pytest.mark.parametrize("text, message", [
    ("code,text\nrnvh3,x\n", "first line"),
    ("product,description\nrnvh3,NO2, sky scans\n", "double quotes"),
    ("product,description\nRNVH3,x\n", "should look like"),
    ("product,description\nrnvh3,\n", "no description"),
    ("product,description\nrnvh3,a\nrnvh3,b\n", "listed twice"),
])
def test_malformed_file_says_what_to_fix(tmp_path, text, message):
    with pytest.raises(ValueError, match=message):
        load_known_products(write(tmp_path, text))

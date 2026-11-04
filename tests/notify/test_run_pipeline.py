from pathlib import Path

from purchasing.cli import main


def test_invalid_csv_keeps_previous_site(tmp_path, capsys):
    data = tmp_path / "data"
    data.mkdir()
    (data / "sku_dim.csv").write_text("unexpected\nPRIVATE-VALUE\n")
    site = tmp_path / "site"
    site.mkdir()
    for name in ("index.html", "order.xlsx"):
        (site / name).write_bytes(b"previous")

    result = main(["run", "--data-dir", str(data), "--out", str(site), "--public-logs"])

    assert result == 2
    assert {p.name: p.read_bytes() for p in site.iterdir()} == {
        "index.html": b"previous", "order.xlsx": b"previous",
    }
    errors = capsys.readouterr().err
    assert "Ошибка данных" in errors and "sku_dim.csv" in errors
    assert "PRIVATE-VALUE" not in errors and str(Path(tmp_path)) not in errors

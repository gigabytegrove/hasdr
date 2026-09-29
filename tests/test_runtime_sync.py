from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_embedded_and_managed_runtimes_match_remote_engine() -> None:
    for filename in ("models.py", "rtl.py", "manager.py"):
        canonical = (ROOT / "bridge" / "sdr_bridge" / filename).read_text()
        embedded = (ROOT / "custom_components" / "rtl_sdr" / "runtime" / filename).read_text()
        managed = (ROOT / "hasdr_engine" / "sdr_bridge" / filename).read_text()

        assert embedded == canonical, f"Embedded runtime drifted: {filename}"
        assert managed == canonical, f"Managed App runtime drifted: {filename}"

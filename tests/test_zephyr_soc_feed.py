from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "src"
SCRIPT = SOURCE_DIR / "zephyr_soc_feed.py"
MODULE_SPEC = importlib.util.spec_from_file_location("zephyr_soc_feed", SCRIPT)
if MODULE_SPEC is None or MODULE_SPEC.loader is None:
    raise RuntimeError("could not load scanner module")
feed = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = feed
MODULE_SPEC.loader.exec_module(feed)


UTC = dt.timezone.utc


def family(name: str, series: list[tuple[str, list[str]]]) -> dict:
    return {
        "name": name,
        "series": [
            {"name": series_name, "socs": [{"name": soc} for soc in socs]}
            for series_name, socs in series
        ],
    }


class SyntheticZephyr:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.recent_time = dt.datetime.now(UTC) - dt.timedelta(days=10)
        self.commit_number = 0
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Feed Test")
        self.git("config", "user.email", "feed-test@example.invalid")

    def git(self, *args: str, env: dict[str, str] | None = None) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        if result.returncode:
            self.fail_command(args, result)
        return result.stdout.strip()

    def fail_command(
        self, args: tuple[str, ...], result: subprocess.CompletedProcess[str]
    ) -> None:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_soc(self, relative: str, families: list[dict]) -> None:
        self.write(relative, yaml.safe_dump({"family": families}, sort_keys=False))

    def commit(self, subject: str, *, old: bool = False) -> str:
        if old:
            when = dt.datetime.now(UTC) - dt.timedelta(days=800)
        else:
            when = self.recent_time + dt.timedelta(minutes=self.commit_number)
            self.commit_number += 1
        timestamp = when.isoformat()
        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": timestamp,
            "GIT_COMMITTER_DATE": timestamp,
        }
        self.git("add", "-A", env=env)
        self.git("commit", "--no-gpg-sign", "-m", subject, env=env)
        return self.git("rev-parse", "HEAD")


class FeedTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo_path = self.root / "zephyr"
        self.repo_path.mkdir()
        self.repo = SyntheticZephyr(self.repo_path)
        self.config_path = self.root / "config.json"

    def write_config(
        self, *, max_feed_items: int = 50, backfill_days: int = 365
    ) -> None:
        config = {
            "schema_version": 1,
            "repo": "unused-because-tests-pass-an-override",
            "revision": "HEAD",
            "catalog_file": "state/catalog.json",
            "cursor_file": "state/cursor.json",
            "history_file": "data/items.json",
            "feed_file": "public/feed.xml",
            "feed_title": "Synthetic new Zephyr families",
            "feed_description": "Only structural family additions.",
            "feed_link": "https://example.test/feed.xml",
            "repository_url": "https://github.com/zephyrproject-rtos/zephyr",
            "max_feed_items": max_feed_items,
            "backfill_days": backfill_days,
        }
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def run_scanner(self, *, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--config",
                str(self.config_path),
                "--repo",
                str(self.repo_path),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            expected,
            result.returncode,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        return result

    def output_json(self, relative: str) -> dict:
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def seed_old_catalog(
        self, families: list[dict] | None = None, *, path: str = "soc/vendor/soc.yml"
    ) -> str:
        families = families or [
            family("existing_family", [("existing_series", ["existing_soc"])])
        ]
        self.repo.write_soc(path, families)
        return self.repo.commit("initial SoC catalog", old=True)


class DetectionEndToEndTests(FeedTestCase):
    def test_only_six301_style_new_family_is_published(self) -> None:
        families = [
            family("silabs_s2", [("efr32mg24", ["efr32mg24b010f1024im48"])]),
            family("legacy_family", [("legacy_series", ["legacy_soc"])]),
        ]
        self.seed_old_catalog(families, path="soc/silabs/soc.yml")

        # A policy-only identifier rename does not touch soc.yml at all.
        self.repo.write("soc/silabs/Kconfig", "config SOC_SILABS_S2\n    bool\n")
        self.repo.commit("soc: silabs: Rename SOC_SILABS_XG* to match policy")

        # Adding a device to an existing family is intentionally out of scope.
        families[0]["series"][0]["socs"].append({"name": "efr32mg24b020f1536im48"})
        self.repo.write_soc("soc/silabs/soc.yml", families)
        self.repo.commit("soc: silabs: Add another S2 SoC")

        # A same-membership family rename must not masquerade as an addition.
        families[1]["name"] = "renamed_family"
        self.repo.write_soc("soc/silabs/soc.yml", families)
        self.repo.commit("soc: vendor: Rename legacy family")

        # This positive example introduces a genuinely new family identity.
        families.append(
            family(
                "silabs_s3",
                [
                    ("sibg301", ["sibg301m104lgh", "sibg301m104lgl"]),
                    ("simg301", ["simg301m103lih"]),
                ],
            )
        )
        self.repo.write_soc("soc/silabs/soc.yml", families)
        six301_commit = self.repo.commit(
            "soc: silabs: Add Six301 SoC and silabs_s3 family"
        )

        self.write_config()
        result = self.run_scanner()
        summary = json.loads(result.stdout)
        self.assertEqual(1, summary["new_items"])

        history = self.output_json("data/items.json")
        self.assertEqual(1, len(history["items"]))
        item = history["items"][0]
        self.assertEqual("silabs_s3", item["family"])
        self.assertEqual(six301_commit, item["commit"])
        self.assertEqual(["sibg301", "simg301"], item["series"])
        self.assertEqual("silabs", item["vendor"])
        self.assertIn("Vendor: silabs.", item["summary"])
        self.assertIn("Series: sibg301, simg301.", item["summary"])
        self.assertIn(
            "SoCs: sibg301m104lgh, sibg301m104lgl, simg301m103lih.", item["summary"]
        )
        self.assertNotIn(
            "renamed_family", [entry["family"] for entry in history["items"]]
        )
        rss_description = ET.parse(self.root / "public/feed.xml").findtext(
            "./channel/item/description"
        )
        self.assertIn("Vendor: silabs.", rss_description or "")
        self.assertIn("Series: sibg301, simg301.", rss_description or "")
        self.assertIn("sibg301m104lgh", rss_description or "")

    def test_duplicate_family_entries_and_files_are_merged(self) -> None:
        self.repo.write_soc(
            "soc/nxp/mcx/soc.yml",
            [
                family("mcxe", [("mcxe24", ["mcxe245"])]),
                family("mcxe", [("mcxe31", ["mcxe316"])]),
            ],
        )
        self.repo.write_soc(
            "soc/nxp/mcx-extra/soc.yml",
            [
                family("mcxe", [("mcxe56", ["mcxe567"])]),
                family("other", [("o1", ["o1a"])]),
            ],
        )
        self.repo.commit("initial split catalog", old=True)
        self.write_config()
        self.run_scanner()

        catalog = self.output_json("state/catalog.json")["families"]
        self.assertEqual(["mcxe24", "mcxe31", "mcxe56"], catalog["mcxe"]["series"])
        self.assertEqual(["mcxe245", "mcxe316", "mcxe567"], catalog["mcxe"]["socs"])
        self.assertEqual(
            ["soc/nxp/mcx-extra/soc.yml", "soc/nxp/mcx/soc.yml"],
            catalog["mcxe"]["source_files"],
        )

    def test_existing_top_level_soc_wrapped_in_family_is_not_published(self) -> None:
        initial = {
            "family": [
                family("existing_family", [("existing_series", ["existing_soc"])])
            ],
            "socs": [{"name": "litex_vexriscv"}],
        }
        self.repo.write("soc/vendor/soc.yml", yaml.safe_dump(initial, sort_keys=False))
        self.repo.commit("initial top-level SoC catalog", old=True)

        reshaped = {
            "family": [
                family("existing_family", [("existing_series", ["existing_soc"])]),
                {"name": "litex", "socs": [{"name": "litex_vexriscv"}]},
            ]
        }
        self.repo.write("soc/vendor/soc.yml", yaml.safe_dump(reshaped, sort_keys=False))
        self.repo.commit("soc: litex: split existing SoC into family metadata")

        self.write_config()
        self.run_scanner()
        self.assertEqual([], self.output_json("data/items.json")["items"])
        self.assertIn("litex", self.output_json("state/catalog.json")["families"])

    def test_mixed_existing_and_new_soc_family_wrapper_is_not_published(self) -> None:
        initial = {
            "family": [
                family("existing_family", [("existing_series", ["existing_soc"])])
            ],
            "socs": [{"name": "versal_rpu"}],
        }
        self.repo.write("soc/vendor/soc.yml", yaml.safe_dump(initial, sort_keys=False))
        self.repo.commit("initial top-level SoC catalog", old=True)

        reshaped = {
            "family": [
                family("existing_family", [("existing_series", ["existing_soc"])]),
                {
                    "name": "amd_versal",
                    "socs": [{"name": "versal_rpu"}, {"name": "versal_apu"}],
                },
            ]
        }
        self.repo.write("soc/vendor/soc.yml", yaml.safe_dump(reshaped, sort_keys=False))
        self.repo.commit("soc: add another device and wrap existing SoC in family")

        self.write_config()
        self.run_scanner()
        self.assertEqual([], self.output_json("data/items.json")["items"])
        self.assertIn("amd_versal", self.output_json("state/catalog.json")["families"])

    def test_normal_cursor_scan_orders_history_and_caps_valid_rss(self) -> None:
        families = [family("existing_family", [("existing_series", ["existing_soc"])])]
        self.seed_old_catalog(families)
        families.append(family("family_a", [("series_a", ["soc_a"])]))
        self.repo.write_soc("soc/vendor/soc.yml", families)
        self.repo.commit("add family A")
        self.write_config(max_feed_items=2)
        first = json.loads(self.run_scanner().stdout)
        self.assertEqual(1, first["new_items"])

        families.append(family("family_b", [("series_b", ["soc_b"])]))
        self.repo.write_soc("soc/vendor/soc.yml", families)
        self.repo.commit("add family B")
        families.append(family("family_c", [("series_c", ["soc_c"])]))
        self.repo.write_soc("soc/vendor/soc.yml", families)
        self.repo.commit("add family C & validate <XML>")

        second = json.loads(self.run_scanner().stdout)
        self.assertEqual(2, second["commits_scanned"])
        self.assertEqual(2, second["new_items"])
        self.assertEqual(3, second["history_items"])
        self.assertEqual(2, second["feed_items"])

        history = self.output_json("data/items.json")
        self.assertEqual(
            ["family_c", "family_b", "family_a"],
            [item["family"] for item in history["items"]],
        )
        xml_path = self.root / "public/feed.xml"
        raw_xml = xml_path.read_text(encoding="utf-8")
        self.assertIn("&amp;", raw_xml)
        self.assertIn("&lt;XML&gt;", raw_xml)
        root = ET.parse(xml_path).getroot()
        rss_items = root.findall("./channel/item")
        self.assertEqual(2, len(rss_items))
        self.assertEqual(
            ["New Zephyr SoC family: family_c", "New Zephyr SoC family: family_b"],
            [item.findtext("title") for item in rss_items],
        )
        for item in rss_items:
            self.assertTrue(item.findtext("guid"))
            self.assertTrue(item.findtext("pubDate"))

    def test_invalid_yaml_fails_closed_without_replacing_outputs(self) -> None:
        self.seed_old_catalog()
        self.write_config()
        self.run_scanner()
        paths = [
            self.root / "state/catalog.json",
            self.root / "state/cursor.json",
            self.root / "data/items.json",
            self.root / "public/feed.xml",
        ]
        before = {path: path.read_bytes() for path in paths}

        self.repo.write("soc/vendor/soc.yml", "family: [this is: not: valid]\n")
        self.repo.commit("break metadata")
        result = self.run_scanner(expected=2)
        self.assertIn("could not parse", result.stderr)
        self.assertEqual(before, {path: path.read_bytes() for path in paths})

    def test_unrelated_upstream_commit_is_a_byte_stable_noop(self) -> None:
        self.seed_old_catalog()
        self.write_config()
        self.run_scanner()
        paths = [
            self.root / "state/catalog.json",
            self.root / "state/cursor.json",
            self.root / "data/items.json",
            self.root / "public/feed.xml",
        ]
        before = {path: path.read_bytes() for path in paths}
        cursor_before = self.output_json("state/cursor.json")["cursor"]

        self.repo.write("drivers/sensor/example.c", "/* unrelated update */\n")
        self.repo.commit("drivers: sensor: unrelated update")
        result = json.loads(self.run_scanner().stdout)

        self.assertEqual(0, result["commits_scanned"])
        self.assertEqual(cursor_before, result["cursor"])
        self.assertEqual(before, {path: path.read_bytes() for path in paths})

        # feed.xml is generated and normally absent from a fresh checkout.
        # Recreate it deterministically without changing durable state.
        feed_path = self.root / "public/feed.xml"
        expected_feed = before[feed_path]
        feed_path.unlink()
        durable_paths = paths[:3]
        durable_before = {path: path.read_bytes() for path in durable_paths}
        fresh_checkout_result = json.loads(self.run_scanner().stdout)
        self.assertEqual(0, fresh_checkout_result["commits_scanned"])
        self.assertEqual(expected_feed, feed_path.read_bytes())
        self.assertEqual(
            durable_before, {path: path.read_bytes() for path in durable_paths}
        )


class DetectionUnitTests(unittest.TestCase):
    def test_rss_rejects_xml_control_characters(self) -> None:
        config = {
            "feed_title": "invalid\x01title",
            "feed_link": "https://example.test/feed.xml",
            "feed_description": "description",
            "repository_url": "https://github.com/zephyrproject-rtos/zephyr",
            "max_feed_items": 50,
        }
        with self.assertRaisesRegex(feed.FeedError, "forbidden by XML 1.0"):
            feed.rss_bytes(config, [], dt.datetime.now(UTC))

    def test_partial_membership_rename_is_rejected(self) -> None:
        before = {
            "old_name": {
                "series": ["series_1"],
                "socs": ["soc_1", "soc_2"],
                "source_files": ["soc/vendor/soc.yml"],
            }
        }
        after = {
            "new_name": {
                "series": ["series_1", "series_2"],
                "socs": ["soc_1", "soc_2", "soc_3"],
                "source_files": ["soc/vendor/soc.yml"],
            }
        }
        self.assertEqual([], feed.new_families(before, after))

    def test_new_family_alongside_unrelated_removal_is_accepted(self) -> None:
        before = {
            "retired": {
                "series": ["old_series"],
                "socs": ["old_soc"],
                "source_files": ["soc/old/soc.yml"],
            }
        }
        after = {
            "new_family": {
                "series": ["new_series"],
                "socs": ["new_soc"],
                "source_files": ["soc/new/soc.yml"],
            }
        }
        self.assertEqual(["new_family"], feed.new_families(before, after))


if __name__ == "__main__":
    unittest.main()

import json, os, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from codex_migrate.common import MigrationError, load
from codex_migrate.exporter import export
from codex_migrate.importer import prepare, apply, verify, rollback
from codex_migrate.cleanup import cleanup_plan, cleanup
from codex_migrate.common import sha, safe_child
from tests.fixtures import OTHER, THREAD, PNG, add_thread, schema


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(
            dir=os.environ.get("CODEX_MIGRATION_TEST_TMP")
        )
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.srcproject = self.root / "source-project"
        self.dstproject = self.root / "target-project"
        schema(self.source, 8)
        schema(self.target, 7)
        add_thread(self.source, self.srcproject)
        add_thread(self.target, self.dstproject, OTHER, with_image=False)
        c = sqlite3.connect(self.target / "state_5.sqlite")
        c.execute(
            "insert into project_roots values(?,?,?)",
            ("synthetic-project", 0, str(self.dstproject)),
        )
        c.commit()
        c.close()

    def tearDown(self):
        self.tmp.cleanup()

    def exported(self, name="stage", baseline=None):
        stage = self.root / name
        export(
            self.source,
            self.source,
            [THREAD],
            stage,
            [[str(self.srcproject), str(self.dstproject)]],
            baseline=baseline,
        )
        return stage

    def test_full_initial_then_refresh_and_conflict(self):
        stage = self.exported()
        plan = load(stage / "payload/plan.json")
        self.assertEqual(plan["images"][0]["kinds"], ["transmitted_bitmap"])
        self.assertFalse(plan["project_files_included"])
        result = prepare(stage, self.target, self.target)
        self.assertTrue(result["compatibility"])
        work = Path(result["prepared_root"])
        with patch("codex_migrate.importer.offline"):
            applied = apply(work, self.target, self.target)
        self.assertEqual(applied["status"], "APPLIED_RESTART_VERIFICATION_PENDING")
        self.assertEqual(verify(stage)["status"], "DATA_VERIFICATION_PASSED")
        c = sqlite3.connect(self.target / "thread_history_1.sqlite")
        self.assertEqual(
            c.execute("select count(*) from thread_items").fetchone()[0], 2
        )
        c.close()
        refresh = self.exported("refresh", stage)
        second = prepare(refresh, self.target, self.target)
        self.assertEqual(second["mode"], "refresh")
        c = sqlite3.connect(self.target / "thread_history_1.sqlite")
        r = c.execute(
            "select item_json from thread_items where thread_id=?", (THREAD,)
        ).fetchone()
        body = json.loads(r[0])
        body["local_edit"] = "Do not overwrite"
        c.execute(
            "update thread_items set item_json=? where thread_id=?",
            (json.dumps(body), THREAD),
        )
        c.commit()
        c.close()
        with self.assertRaisesRegex(MigrationError, "local changes"):
            prepare(refresh, self.target, self.target)

    def test_changed_prepared_file_is_rejected(self):
        stage = self.exported()
        result = prepare(stage, self.target, self.target)
        file = next(
            x["prepared"] for x in result["targets"] if x["role"] == "session-log"
        )
        Path(file).write_bytes(b"changed\n")
        with (
            patch("codex_migrate.importer.offline"),
            self.assertRaisesRegex(MigrationError, "Prepared file changed"),
        ):
            apply(Path(result["prepared_root"]), self.target, self.target)

    def test_publish_failure_restores_all_prior_data(self):
        stage = self.exported()
        result = prepare(stage, self.target, self.target)
        before = {
            n: (self.target / n).read_bytes()
            for n in ("state_5.sqlite", "thread_history_1.sqlite")
        }
        calls = 0
        import os

        original = os.replace

        def fail_second(a, b):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("synthetic publication failure")
            return original(a, b)

        with (
            patch("codex_migrate.importer.offline"),
            patch("codex_migrate.importer.os.replace", side_effect=fail_second),
            self.assertRaises(OSError),
        ):
            apply(Path(result["prepared_root"]), self.target, self.target)
        for n, data in before.items():
            self.assertEqual((self.target / n).read_bytes(), data)
        self.assertFalse((stage / "apply-result.json").exists())
        self.assertTrue((stage / "rollback-result.json").exists())

    def test_cleanup_requires_user_selection_and_keeps_live_images(self):
        stage = self.exported()
        result = prepare(stage, self.target, self.target)
        with patch("codex_migrate.importer.offline"):
            applied = apply(Path(result["prepared_root"]), self.target, self.target)
        verify(stage)
        plan = cleanup_plan(stage)
        backup = Path(applied["backup"]).name
        self.assertTrue(all(not row["delete_selected"] for row in plan["entries"]))
        with self.assertRaisesRegex(MigrationError, "Backups are kept"):
            cleanup(stage / "cleanup-plan.json", [backup])
        cleanup(stage / "cleanup-plan.json", ["bundle.zip", "snapshots"])
        self.assertTrue(Path(applied["backup"]).exists())
        self.assertEqual(next(Path(result["image_root"]).iterdir()).read_bytes(), PNG)

    def test_apply_refuses_changes_since_prepare(self):
        stage = self.exported()
        result = prepare(stage, self.target, self.target)
        c = sqlite3.connect(self.target / "state_5.sqlite")
        c.execute(
            "update threads set name=? where id=?", ("Later unrelated work", OTHER)
        )
        c.commit()
        c.close()
        with (
            patch("codex_migrate.importer.offline"),
            self.assertRaisesRegex(MigrationError, "changed since prepare"),
        ):
            apply(Path(result["prepared_root"]), self.target, self.target)

    def test_unknown_schema_and_nontrivial_root_relation_are_blocked(self):
        c = sqlite3.connect(self.source / "thread_history_1.sqlite")
        c.execute("update thread_turns set root_turn_id=?", ("different-root",))
        c.commit()
        c.close()
        with self.assertRaisesRegex(MigrationError, "nontrivial root"):
            prepare(self.exported(), self.target, self.target)
        c = sqlite3.connect(self.source / "thread_history_1.sqlite")
        c.execute(
            "insert into _sqlx_migrations values(9,?,1)", ("Unknown fixture schema",)
        )
        c.commit()
        c.close()
        with self.assertRaisesRegex(MigrationError, "Unsupported"):
            prepare(self.exported("future"), self.target, self.target)
        c = sqlite3.connect(self.target / "thread_history_1.sqlite")
        c.execute("insert into _sqlx_migrations values(8,?,1)", ("thread turn root",))
        c.execute(
            "insert into _sqlx_migrations values(9,?,1)", ("Unknown fixture schema",)
        )
        c.commit()
        c.close()
        with self.assertRaisesRegex(
            MigrationError, "Unsupported database migration version"
        ):
            prepare(self.exported("equal-future"), self.target, self.target)

    def test_export_preserves_source_and_recovered_image_bytes(self):
        before = {p: sha(p) for p in self.source.rglob("*") if p.is_file()}
        stage = self.exported()
        self.assertEqual(before, {p: sha(p) for p in before})
        self.assertEqual(next((stage / "payload/images").iterdir()).read_bytes(), PNG)
        with self.assertRaisesRegex(MigrationError, "Unsafe"):
            safe_child(self.root, "../escape")

    def test_rollback_does_not_overwrite_later_work(self):
        stage = self.exported()
        result = prepare(stage, self.target, self.target)
        with patch("codex_migrate.importer.offline"):
            applied = apply(Path(result["prepared_root"]), self.target, self.target)
        c = sqlite3.connect(self.target / "state_5.sqlite")
        c.execute("update threads set name=? where id=?", ("Later work", OTHER))
        c.commit()
        c.close()
        with (
            patch("codex_migrate.importer.offline"),
            self.assertRaisesRegex(MigrationError, "changed after migration"),
        ):
            rollback(Path(applied["backup"]), self.target, self.target)

    def test_desktop_project_assignments_and_remote_rows_are_preserved(self):
        for home in (self.source, self.target):
            (home / "sqlite").mkdir()
            c = sqlite3.connect(home / "sqlite/codex-dev.db")
            c.execute(
                "create table local_thread_catalog(host_id TEXT,thread_id TEXT,display_title TEXT,source_updated_at INTEGER,source_recency_at INTEGER,cwd TEXT,project_id TEXT,missing_candidate INTEGER,PRIMARY KEY(host_id,thread_id))"
            )
            if home == self.source:
                c.execute(
                    "insert into local_thread_catalog values(?,?,?,?,?,?,?,?)",
                    (
                        "local",
                        THREAD,
                        "Synthetic desktop chat",
                        1,
                        1,
                        str(self.srcproject),
                        "synthetic-source",
                        0,
                    ),
                )
            else:
                c.execute(
                    "insert into local_thread_catalog values(?,?,?,?,?,?,?,?)",
                    (
                        "remote-fixture",
                        THREAD,
                        "Unrelated remote chat",
                        2,
                        2,
                        "synthetic-remote",
                        "remote-project",
                        0,
                    ),
                )
            c.commit()
            c.close()
        obj = {
            "local-projects": {"desktop-target": {"path": str(self.dstproject)}},
            "unrelated-ui-setting": "keep",
        }
        (self.target / ".codex-global-state.json").write_text(
            json.dumps(obj), encoding="utf-8"
        )
        stage = self.exported()
        r = prepare(stage, self.target, self.target)
        with patch("codex_migrate.importer.offline"):
            apply(Path(r["prepared_root"]), self.target, self.target)
        self.assertEqual(verify(stage)["status"], "DATA_VERIFICATION_PASSED")
        after = load(self.target / ".codex-global-state.json")
        self.assertEqual(after["unrelated-ui-setting"], "keep")
        self.assertEqual(
            after["thread-project-assignments"][THREAD]["projectId"], "desktop-target"
        )
        c = sqlite3.connect(self.target / "sqlite/codex-dev.db")
        self.assertEqual(
            c.execute(
                "select display_title from local_thread_catalog where host_id='remote-fixture'"
            ).fetchone()[0],
            "Unrelated remote chat",
        )
        c.close()
        after["thread-project-assignments"][THREAD]["projectId"] = "changed"
        (self.target / ".codex-global-state.json").write_text(
            json.dumps(after), encoding="utf-8"
        )
        self.assertEqual(verify(stage)["status"], "DATA_VERIFICATION_FAILED")

    def test_refresh_rejects_changed_local_image_bytes(self):
        stage = self.exported()
        r = prepare(stage, self.target, self.target)
        with patch("codex_migrate.importer.offline"):
            apply(Path(r["prepared_root"]), self.target, self.target)
        next(Path(r["image_root"]).iterdir()).write_bytes(PNG + b"changed")
        with self.assertRaisesRegex(MigrationError, "image content changed"):
            prepare(self.exported("refresh", stage), self.target, self.target)

    def test_rollback_detects_wal_only_later_work(self):
        stage = self.exported()
        r = prepare(stage, self.target, self.target)
        with patch("codex_migrate.importer.offline"):
            applied = apply(Path(r["prepared_root"]), self.target, self.target)
        c = sqlite3.connect(self.target / "state_5.sqlite")
        c.execute("pragma journal_mode=wal")
        c.execute("pragma wal_autocheckpoint=0")
        c.commit()
        before = sha(self.target / "state_5.sqlite")
        c.execute(
            "update threads set name=? where id=?", ("WAL-only later work", OTHER)
        )
        c.commit()
        self.assertEqual(sha(self.target / "state_5.sqlite"), before)
        try:
            with (
                patch("codex_migrate.importer.offline"),
                self.assertRaisesRegex(MigrationError, "including WAL"),
            ):
                rollback(Path(applied["backup"]), self.target, self.target)
        finally:
            c.close()

    def test_transfer_bind_failure_revokes_token(self):
        from codex_migrate.transport import serve

        stage = self.exported()
        connection = self.root / "connection-test.json"
        with (
            patch(
                "codex_migrate.transport.http.server.ThreadingHTTPServer",
                side_effect=OSError("synthetic bind failure"),
            ),
            self.assertRaises(OSError),
        ):
            serve(stage, "127.0.0.1", "127.0.0.1", 49473, 1, connection)
        self.assertEqual(load(connection)["status"], "REVOKED")
        self.assertNotIn("token", load(connection))

    def test_receive_resumes_and_checks_selected_payload(self):
        import io
        from codex_migrate.transport import receive
        from codex_migrate.common import dump

        stage = self.exported()
        output = self.root / "received"
        output.mkdir()
        archive = (stage / "bundle.zip").read_bytes()
        (output / "bundle.zip.part").write_bytes(archive[:10])
        connection = self.root / "connection-test.json"
        dump(
            connection,
            {
                "format_version": 1,
                "host": "127.0.0.1",
                "port": 49473,
                "token": "synthetic-fixture",
                "run_id": load(stage / "manifest.json")["run_id"],
                "archive_sha256": sha(stage / "bundle.zip"),
                "manifest_sha256": sha(stage / "manifest.json"),
            },
        )
        requests = []

        def open_request(request, timeout):
            requests.append(request)
            data = (
                (stage / "manifest.json").read_bytes()
                if request.full_url.endswith("manifest.json")
                else archive[10:]
            )
            response = io.BytesIO(data)
            response.status = 206 if request.full_url.endswith("bundle.zip") else 200
            return response

        with patch("codex_migrate.transport.urllib.request.build_opener") as opener:
            opener.return_value.open.side_effect = open_request
            self.assertEqual(
                receive(connection, output)["status"], "RECEIVED_AND_VERIFIED"
            )
        self.assertEqual(requests[1].get_header("Range"), "bytes=10-")
        self.assertEqual(next((output / "payload/images").iterdir()).read_bytes(), PNG)
        self.assertFalse((output / "apply-result.json").exists())

    def test_manual_rollback_restores_initial_databases_and_new_files(self):
        stage = self.exported()
        before = {p.name: sha(p) for p in self.target.glob("*.sqlite")}
        r = prepare(stage, self.target, self.target)
        with patch("codex_migrate.importer.offline"):
            applied = apply(Path(r["prepared_root"]), self.target, self.target)
            self.assertEqual(
                rollback(Path(applied["backup"]), self.target, self.target)["status"],
                "ROLLED_BACK",
            )
        self.assertEqual(before, {p.name: sha(p) for p in self.target.glob("*.sqlite")})
        self.assertFalse((self.target / "session_index.jsonl").exists())
        self.assertEqual(list(Path(r["image_root"]).iterdir()), [])


if __name__ == "__main__":
    unittest.main()

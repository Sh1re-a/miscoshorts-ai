from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.render_session import RenderWorkspace


class RenderWorkspaceTests(unittest.TestCase):
    def test_promote_moves_workspace_into_final_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir) / "jobs"
            workspace = RenderWorkspace.create(fingerprint="fingerprint01", job_id="job01", base_dir=base_dir)
            original_workspace_dir = workspace.workspace_dir
            clip_path = workspace.clips_dir / "clip.mp4"
            clip_path.write_text("new-output", encoding="utf-8")

            promoted_dir = workspace.promote()

            self.assertEqual(promoted_dir, base_dir / "fingerprint01")
            self.assertTrue((promoted_dir / "clips" / "clip.mp4").exists())
            self.assertFalse(original_workspace_dir.exists())

    def test_promote_restores_previous_output_if_move_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir) / "jobs"
            workspace = RenderWorkspace.create(fingerprint="fingerprint02", job_id="job02", base_dir=base_dir)
            (workspace.clips_dir / "clip.mp4").write_text("new-output", encoding="utf-8")
            final_output_dir = base_dir / "fingerprint02"
            (final_output_dir / "clips").mkdir(parents=True, exist_ok=True)
            preserved_file = final_output_dir / "clips" / "old.mp4"
            preserved_file.write_text("old-output", encoding="utf-8")

            real_move = __import__("shutil").move
            move_calls: list[tuple[str, str]] = []

            def flaky_move(src: str, dst: str):
                move_calls.append((src, dst))
                if len(move_calls) == 2:
                    raise RuntimeError("simulated promotion failure")
                return real_move(src, dst)

            with patch("app.render_session.shutil.move", side_effect=flaky_move):
                with self.assertRaisesRegex(RuntimeError, "simulated promotion failure"):
                    workspace.promote()

            self.assertTrue(preserved_file.exists())
            self.assertEqual(preserved_file.read_text(encoding="utf-8"), "old-output")
            self.assertTrue((workspace.workspace_dir / "clips" / "clip.mp4").exists())


if __name__ == "__main__":
    unittest.main()

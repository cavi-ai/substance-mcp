import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path

from cavi_substance_mcp.sampler.launch import _tail_progress
from cavi_substance_mcp.sampler.operations import _wait_files


class ExportWaitTests(unittest.TestCase):
    def test_a_pause_between_writes_does_not_end_the_wait(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            target = directory / "Rock_basecolor.png"
            target.write_text("start")

            def flush():
                for _ in range(3):
                    time.sleep(0.5)
                    with target.open("a") as stream:
                        stream.write("-late")

            writer = threading.Thread(target=flush)
            writer.start()
            try:
                files = _wait_files(directory, timeout=10, poll=0.05)
                size_at_return = files[0].stat().st_size
            finally:
                writer.join()
            self.assertEqual(size_at_return, len("start" + "-late" * 3))

    def test_a_map_named_inside_another_file_name_is_still_missing(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name in ("NormalRock_basecolor", "NormalRock_occlusionroughnessmetallic"):
                (directory / f"{name}.png").write_text("fake")
            with self.assertRaisesRegex(RuntimeError, "missing maps: normal, metallic;"):
                _wait_files(directory, timeout=5, poll=0.05, grace=0.1, settle=0.1,
                            expected=("basecolor", "normal", "metallic",
                                      "occlusionroughnessmetallic"))


class ProgressTailTests(unittest.TestCase):
    def test_a_line_written_in_two_parts_is_delivered_once_complete(self):
        events = []

        async def tail(path):
            stop = asyncio.Event()
            task = asyncio.create_task(_tail_progress(path, events.append, stop))
            with path.open("w", encoding="utf-8") as stream:
                stream.write('{"step": 1}\n{"step"')
                stream.flush()
                await asyncio.sleep(0.5)
                stream.write(': 2}\n')
            await asyncio.sleep(0.5)
            stop.set()
            await task

        with tempfile.TemporaryDirectory() as temp:
            asyncio.run(tail(Path(temp) / "progress.jsonl"))
        self.assertEqual(events, [{"step": 1}, {"step": 2}])


if __name__ == "__main__":
    unittest.main()

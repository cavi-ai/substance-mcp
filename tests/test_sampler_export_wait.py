import tempfile
import threading
import time
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()

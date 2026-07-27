import importlib.util
import logging
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
LOGGING_HELPER = REPO_ROOT / "examples" / "demo" / "_projectairsim_logging.py"


class ProjectAirSimLoggingTests(unittest.TestCase):
    """验证 Project AirSim 示例不会启用第三方默认文件日志。"""

    def test_demo_logging_uses_console_without_file_handler(self) -> None:
        """确保 demo 预配置控制台日志，阻止 Project AirSim 创建默认日志文件。"""

        spec = importlib.util.spec_from_file_location("fleetdock_projectairsim_logging_test", LOGGING_HELPER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        logger = logging.getLogger("projectairsim")
        original_handlers = list(logger.handlers)
        original_level = logger.level
        original_propagate = logger.propagate
        try:
            logger.handlers.clear()
            module.configure_projectairsim_console_logging()

            self.assertTrue(logger.hasHandlers())
            self.assertFalse(any(isinstance(handler, logging.FileHandler) for handler in logger.handlers))
            self.assertTrue(
                any(
                    isinstance(handler, logging.StreamHandler) and handler.stream is sys.stdout
                    for handler in logger.handlers
                )
            )
        finally:
            for handler in logger.handlers:
                if handler not in original_handlers:
                    handler.close()
            logger.handlers[:] = original_handlers
            logger.setLevel(original_level)
            logger.propagate = original_propagate


if __name__ == "__main__":
    unittest.main()

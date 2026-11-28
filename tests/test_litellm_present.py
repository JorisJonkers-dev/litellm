"""Inside the image the Router tests must run, not skip: they skip when litellm cannot be imported."""
import importlib.util
import os
import unittest


class LitellmPresent(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("REQUIRE_LITELLM"), "set by `npm run test:router`, which runs the suite in the image")
    def test_litellm_is_importable_so_the_router_tests_do_not_skip(self):
        self.assertIsNotNone(importlib.util.find_spec("litellm"))


if __name__ == "__main__":
    unittest.main()

import unittest
from shadow_tge_candidate import validate_candidate


class CandidateTests(unittest.TestCase):
    def test_rejects_nonofficial_fragment_without_header(self):
        html = b"<table><tr><td>2026-11-17_H01</td><td>60</td><td>738,93</td></tr></table>"
        with self.assertRaises(ValueError):
            validate_candidate("2026-11-17", html)

    def test_rejects_missing_prices(self):
        html = b"<table id='rdn'><thead><tr><th></th></tr></thead><tbody></tbody></table>"
        with self.assertRaises(ValueError):
            validate_candidate("2026-11-17", html)


if __name__ == "__main__":
    unittest.main()

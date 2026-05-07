import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DashboardLayoutTests(unittest.TestCase):
    def test_main_dashboard_uses_full_height_work_surface(self):
        template = (PROJECT_ROOT / "src" / "templates" / "base.html").read_text()
        styles = (PROJECT_ROOT / "src" / "static" / "style.css").read_text()

        self.assertIn('class="dashboard-workspace"', template)
        self.assertIn('class="dashboard-primary"', template)
        self.assertIn('class="dashboard-rail"', template)
        self.assertLess(template.index('class="dashboard-primary"'), template.index('class="dashboard-rail"'))
        self.assertNotIn('class="row g-3 mb-3"', template)
        self.assertIn("min-height: calc(100vh - 156px)", styles)
        self.assertIn("grid-template-columns: minmax(0, 1fr) minmax(300px, 26vw)", styles)
        self.assertIn(".recent-table-wrap", styles)
        self.assertIn("height: 100%", styles)


if __name__ == "__main__":
    unittest.main()

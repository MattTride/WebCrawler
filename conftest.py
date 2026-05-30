import sys
from pathlib import Path

# Make crawler_core importable from tests/ without packaging the project.
sys.path.insert(0, str(Path(__file__).parent))

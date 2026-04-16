from __future__ import annotations

import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
APP_DIR = ROOT_DIR / "pacifica-edge"
APP_PATH = APP_DIR / "main.py"

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

spec = spec_from_file_location("pacificaedge_main", APP_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Unable to load FastAPI app from {APP_PATH}")

module = module_from_spec(spec)
spec.loader.exec_module(module)
app = module.app

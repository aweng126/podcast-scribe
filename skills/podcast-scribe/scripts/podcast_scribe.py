#!/usr/bin/env python3
"""Run from a skill checkout without installing the package."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from podcast_scribe.cli import main

raise SystemExit(main())

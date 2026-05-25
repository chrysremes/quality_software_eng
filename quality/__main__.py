"""Allow invocation via `python -m quality`."""

import sys

from .run_evaluation import main

sys.exit(main())

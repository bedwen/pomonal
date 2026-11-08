"""Launch Pomonal without system-wide installation."""

import sys

sys.dont_write_bytecode = True

from .presentation import main

raise SystemExit(main())

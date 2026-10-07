"""Allow ``python -m afa_pipeline``."""

import sys

from afa_pipeline.cli import main

if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
from __future__ import annotations

import sys
from .main import main

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)

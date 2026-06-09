#!/usr/bin/env python3
"""OutreachFlow — entry point.

Usage:
    python main.py --domain stripe.com
    python main.py --domain stripe.com --dry-run
    python main.py --domain stripe.com --mock
"""

from outreachflow.main import main

if __name__ == "__main__":
    main()

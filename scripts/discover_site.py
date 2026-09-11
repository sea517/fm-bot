"""Discover freelancermap inbox URL and save to .env."""

import asyncio
import sys

from src.freelancermap.discover import discover

if __name__ == "__main__":
    headless = "--headed" not in sys.argv
    asyncio.run(discover(headless=headless))

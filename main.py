#!/usr/bin/env python3
"""Trading Strategy Execution Platform - Main Entry Point"""

import sys
import os

# Ensure this project root is importable regardless of the current working dir
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli import PlatformCLI


def main():
    """Main entry point"""
    cli = PlatformCLI()
    cli.run()


if __name__ == '__main__':
    main()

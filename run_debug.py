#!/usr/bin/env python3
import subprocess
import sys

result = subprocess.run(
    [sys.executable, "debug_section1_flags.py"],
    cwd=r"c:\Users\Sachin\OneDrive\Desktop\algo",
    capture_output=False,
    text=True
)

sys.exit(result.returncode)

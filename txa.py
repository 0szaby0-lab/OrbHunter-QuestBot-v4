import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

os.environ["TXA_LAUNCHED"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

from bot import run_bot

if __name__ == "__main__":
    run_bot()


import sys
from pathlib import Path

log_file = Path(__file__).resolve().parent / "agent.log"
sys.stdout = open(log_file, "w", encoding="utf-8", buffering=1)
sys.stderr = sys.stdout

import multicontrol_agent

if __name__ == "__main__":
    multicontrol_agent.main()

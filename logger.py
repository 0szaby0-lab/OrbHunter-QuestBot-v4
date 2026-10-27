from datetime import datetime
from config import DEBUG, LOG_PROGRESS

class Colors:
    RESET   = "\033[0m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    CYAN    = "\033[96m"
    MAGENTA = "\033[95m"
    BLUE    = "\033[94m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"

def log(msg: str, level: str = "info"):
    ts = datetime.now().strftime("%H:%M:%S")
    
    # Custom stylized prefixes with colors and emojis
    prefix = {
        "info":     f"{Colors.CYAN}{Colors.BOLD}[INFO]{Colors.RESET} ℹ️",
        "ok":       f"{Colors.GREEN}{Colors.BOLD}[SUCCESS]{Colors.RESET} ✅",
        "warn":     f"{Colors.YELLOW}{Colors.BOLD}[WARNING]{Colors.RESET} ⚠️",
        "error":    f"{Colors.RED}{Colors.BOLD}[ERROR]{Colors.RESET} ❌",
        "progress": f"{Colors.BLUE}[PROGRESS]{Colors.RESET} ⚙️",
        "debug":    f"{Colors.MAGENTA}{Colors.DIM}[DEBUG]{Colors.RESET} 🔍",
    }.get(level, f"[{level.upper()}]")

    if level == "debug" and not DEBUG:
        return
    if LOG_PROGRESS or level != "progress":
        print(f"{Colors.DIM}[{ts}]{Colors.RESET} {prefix} {msg}")

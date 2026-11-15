"""Plain ASCII terminal identity; no dependencies or terminal control sequences."""
from faultcore import __version__

LOGO = ' _____ _   _   _ _   _____ ____ ___  ____  _____\n|  ___/ \\ | | | | | |_   _/ ___/ _ \\|  _ \\| ____|\n| |_ / _ \\| | | | |   | || |  | | | | |_) |  _|\n|  _/ ___ \\ |_| | |___| || |__| |_| |  _ <| |___\n|_|/_/   \\_\\___/|_____|_| \\____\\___/|_| \\_\\_____|\n'

def banner() -> str:
    return LOGO + f"\nUNIT-47  |  FaultCore {__version__}  |  Lab MVP\nReproduce. Minimize. Document."

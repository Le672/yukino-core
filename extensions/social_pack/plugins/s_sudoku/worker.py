from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from puzzles import handle as run


def handle(text, context):
    return run("s_sudoku", text, context)

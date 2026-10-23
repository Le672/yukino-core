"""Yukino discovery plugin entry point."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from network import handle as run

def handle(text,context):
    return run('d_air',text,context)

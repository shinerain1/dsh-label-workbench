"""Install the Python backend into the explicitly selected interpreter/environment."""
import subprocess
import sys
from pathlib import Path

if __name__=="__main__":
    project=Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable,"-m","pip","install",str(project)],check=True)

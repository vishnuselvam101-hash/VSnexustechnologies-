import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from vnxdna.experiments.reproducibility import environment
import json
print(json.dumps(environment(),indent=2))

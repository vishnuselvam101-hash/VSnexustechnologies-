import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from vnxdna.experiments.runner import run
for e in [f'E{i:03d}' for i in range(1,9)]: print(e,run(e)['summary'])

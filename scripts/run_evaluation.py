"""Run the deterministic Week-9 evaluation baseline."""
import json
from app.services.evaluation import run_evaluation

print(json.dumps(run_evaluation(), ensure_ascii=False, indent=2))

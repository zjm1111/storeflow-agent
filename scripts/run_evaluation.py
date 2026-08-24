"""Run the deterministic baseline; --bailian explicitly enables remote evaluation."""
import argparse
import json
from app.services.evaluation import run_evaluation, run_optional_bailian_retrieval_evaluation

parser = argparse.ArgumentParser()
parser.add_argument("--bailian", action="store_true", help="Run the paid BaiLian embedding/rerank comparison on frozen simulated data.")
parser.add_argument("--max-queries", type=int, default=12, help="Remote evaluation query cap; use 0 for all 48 primary questions.")
args = parser.parse_args()

report = run_evaluation()
if args.bailian:
    report["optional_bailian"] = run_optional_bailian_retrieval_evaluation(None if args.max_queries == 0 else args.max_queries)
print(json.dumps(report, ensure_ascii=False, indent=2))

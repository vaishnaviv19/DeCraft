# scripts/run_blueprint_and_plan.py
from pathlib import Path
import sys

root = Path(__file__).parent.parent
sys.path.insert(0, str(root))

from src.blueprint.build_blueprint import build_blueprint
from src.planner.build_plan import build_document_plan


def main():
    pdf = root / "data" / "input" / "sample_input_1.pdf"
    print(f"[RUN] Building blueprint from: {pdf}")
    bp = build_blueprint(pdf, project_root=root)
    print(f"[RUN] Blueprint built with {len(bp.get('text_blocks',[]))} text blocks, {len(bp.get('tables',[]))} tables")
    print(f"[RUN] Building document plan...")
    build_document_plan(bp)
    print(f"[RUN] Done. Blueprint + Plan generated.")


if __name__ == "__main__":
    main()

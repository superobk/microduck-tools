"""Run the kit's Studio with the same interpreter selected by duck.sh."""

from pathlib import Path
import sys

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "studio/source/microduck-tools-0.6.0")
)
from studio.manage import main

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Studio停止：" + str(error), file=sys.stderr)
        raise SystemExit(1)

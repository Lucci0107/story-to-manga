"""APIが生成した固定SELECTを独立プロセスで実行する。公開APIではない。"""

import json
import sys
from contextlib import closing
from pathlib import Path

from .observability import connect_read_only, safe_row


def main() -> int:
    try:
        request = json.loads(sys.stdin.read(16000))
        with closing(connect_read_only(Path(request["path"]))) as connection:
            rows = [
                safe_row(dict(row))
                for row in connection.execute(request["sql"], request["values"])
            ]
        sys.stdout.write(json.dumps(rows))
        return 0
    except Exception:  # noqa: BLE001
        # DBパス・SQL・例外本文をstderrにも出さない。
        return 1


if __name__ == "__main__":
    sys.exit(main())

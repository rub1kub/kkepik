"""Wait for the active campaign's primary schedule, then send its saved post."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ad_dispatch import run

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Resume a paused campaign and retry definitive rejections')
    args = parser.parse_args()
    try:
        asyncio.run(run(send=args.send, resume=args.resume))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as error:
        print(json.dumps({'status':'stopped','error_type':type(error).__name__}), flush=True)
        sys.exit(1)

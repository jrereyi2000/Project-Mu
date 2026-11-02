"""Pure offline profile preview. No database or save-file access."""
import argparse
import json
from pathlib import Path
from personality.core import Identity, initialize

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--universe', required=True)
parser.add_argument('--role', choices=('player','coach','gm','owner','scout','writer'), required=True)
parser.add_argument('--birth-key', required=True)
parser.add_argument('--origin', choices=('generated','real','unknown'), default='generated')
parser.add_argument('--season', type=int, required=True)
parser.add_argument('--out', type=Path)
args=parser.parse_args()
p=initialize(Identity(args.universe,args.role,args.birth_key), args.origin,args.season)
text=json.dumps(p,sort_keys=True,indent=2)+'\n'
if args.out:
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(text)
else:
    print(text,end='')

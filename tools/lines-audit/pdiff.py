import json,sys
a={r['stem']:r for r in json.load(open(f'/home/claude/work/runs/{sys.argv[1]}/{sys.argv[3]}.json'))}
b={r['stem']:r for r in json.load(open(f'/home/claude/work/runs/{sys.argv[2]}/{sys.argv[3]}.json'))}
for k in a:
    if (a[k]['m'],a[k]['o'])!=(b[k]['m'],b[k]['o']): print(f"{a[k]['stratum']:10} {k:32} t {a[k]['t']:3}  m {a[k]['m']:3}->{b[k]['m']:3}  ours {a[k]['o']:3}->{b[k]['o']:3}")

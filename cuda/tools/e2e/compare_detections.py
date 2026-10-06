#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Compare per-frame detections of two run_e2e.sh runs (detections.jsonl) and, for binaries built with
BENCHMARK enabled in src/main.cpp, their per-frame and per-kernel times (vp.log).

usage: compare_detections.py <run dir A> <run dir B>
"""
import json, math, re, statistics, sys

def load(run):
    frames = {}
    for line in open(f'{run}/detections.jsonl'):
        d = json.loads(line)
        frames.setdefault(d['frame'], d)  # keep first packet per frame
    return frames

def times(run):
    t = [float(m.group(1)) for m in re.finditer(r'\] time ([0-9.]+) ms', open(f'{run}/vp.log').read())]
    return t

def summary(t):
    if not t:
        return 'n/a'
    s = sorted(t)
    return f'n={len(t)} mean {statistics.mean(t):.2f} ms, median {s[len(s)//2]:.2f} ms, p95 {s[int(len(s)*0.95)]:.2f} ms'

a_name, b_name = sys.argv[1], sys.argv[2]
A, B = load(a_name), load(b_name)
common = sorted(set(A) & set(B))
print(f'{a_name}: {len(A)} frames ({min(A)}..{max(A)}), {b_name}: {len(B)} frames ({min(B)}..{max(B)}), common {len(common)}')

identical = 0
count_diff = 0
pos_diff = []
ori_diff = []
conf_diff = []
robot_total = ball_total = 0
id_mismatch = 0
for f in common:
    a, b = A[f], B[f]
    same = True
    for team in ('yellow', 'blue'):
        ra = {r['id']: r for r in a[team]}
        rb = {r['id']: r for r in b[team]}
        robot_total += len(ra)
        if set(ra) != set(rb):
            id_mismatch += 1
            same = False
        for i in set(ra) & set(rb):
            pos_diff.append(math.hypot(ra[i]['x'] - rb[i]['x'], ra[i]['y'] - rb[i]['y']))
            d = abs(ra[i]['o'] - rb[i]['o'])
            ori_diff.append(min(d, 2*math.pi - d))
            conf_diff.append(abs(ra[i]['c'] - rb[i]['c']))
            same &= ra[i] == rb[i]
    ball_total += len(a['balls'])
    if len(a['balls']) != len(b['balls']):
        count_diff += 1
        same = False
    else:
        for ba in a['balls']:
            nearest = min(b['balls'], key=lambda bb: math.hypot(ba['x'] - bb['x'], ba['y'] - bb['y']))
            pos_diff.append(math.hypot(ba['x'] - nearest['x'], ba['y'] - nearest['y']))
            conf_diff.append(abs(ba['c'] - nearest['c']))
        same &= sorted(map(json.dumps, a['balls'])) == sorted(map(json.dumps, b['balls']))
    identical += same

print(f'  bit-identical frames: {identical}/{len(common)}; robot id-set mismatches: {id_mismatch}; ball count mismatches: {count_diff}')
print(f'  robots per frame (A): {robot_total/len(common):.2f}, balls per frame (A): {ball_total/len(common):.2f}')
if pos_diff:
    print(f'  max position diff {max(pos_diff):.6f} mm, max orientation diff {max(ori_diff) if ori_diff else 0:.6f} rad, max confidence diff {max(conf_diff):.6f}')
def kernels(run):
    rows = [list(map(float, re.findall(r'([0-9.]+)ms', l))) for l in open(f'{run}/vp.log') if re.fullmatch(r'(\s*[0-9.]+ms)+\s*', l)]
    rows = [r for r in rows if len(r) == 8]  # stream kernel of the previous frame + 7 detection kernels
    if not rows:
        return 'n/a'
    names = ['stream(prev)', 'raw2quad', 'resampling', 'gradientDot', 'satH', 'satV', 'satBlobCenter', 'blobList']
    avg = [statistics.mean(c) for c in zip(*rows)]
    return ', '.join(f'{n} {v:.3f}' for n, v in zip(names, avg)) + f' | sum {sum(avg):.2f} ms (n={len(rows)})'

for name in (a_name, b_name):
    print(f'  frame time {name}: {summary(times(name))}')
    print(f'    kernel ms {name}: {kernels(name)}')

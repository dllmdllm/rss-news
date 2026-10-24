"""Produce an offline review plan, without modifying snapshots or running AI.

History is explicitly supplied and target IDs explicitly selected. The plan is
not a publication instruction: revalidate targets again before a separately
approved restore. Only summary fields change; body/media and other edits stay.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.summary_recovery import body_valid, recovery_action


def read_json(path, limit=8*1024*1024):
    if path.stat().st_size > limit:
        raise ValueError('snapshot file exceeds bound')
    return json.loads(path.read_text(encoding='utf-8'))


def load_rows(docs):
    payload = read_json(docs/'data/articles.json', 32*1024*1024)
    rows = payload['articles']
    by_id = {row['id']:row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError('duplicate snapshot identities')
    return payload.get('updated'), by_id


def load_body(docs, row):
    aid = row['id']
    if not re.fullmatch(r'[a-f0-9]{12}', aid):
        raise ValueError('invalid article id')
    sidecar = read_json(docs/'data/content'/f'{aid}.json')
    result = dict(row, content=sidecar.get('content'))
    if (sidecar.get('version') != 1 or not body_valid(result) or
            sidecar.get('content_hash') != result.get('content_hash') or
            sidecar.get('quality') != result.get('content_quality')):
        raise ValueError('snapshot body/provenance mismatch')
    return result


def plan(current_docs, histories, target_ids):
    snapshot, rows = load_rows(current_docs)
    old_rows = [(path, load_rows(path)[1]) for path in histories]
    actions, rejected = [], []
    for aid, row in rows.items():
        points = row.get('summary_points') or []
        if aid not in target_ids and not (row.get('summary_status') == 'partial' and len(points) == 5):
            continue
        try:
            current = load_body(current_docs, row)
            action = recovery_action(current)
            if aid in target_ids:
                for path, prior in old_rows:
                    if aid in prior:
                        historical = load_body(path, prior[aid])
                        restored = recovery_action(current, historical)
                        if restored:
                            action = dict(restored, historical_snapshot=path.parent.name)
                            break
            if action:
                actions.append(action)
            elif aid in target_ids:
                rejected.append({'id':aid, 'reason':'no unchanged, fully source-verified historical result'})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            rejected.append({'id':aid, 'reason':str(exc)[:160]})
    for aid in sorted(target_ids - rows.keys()):
        rejected.append({'id':aid, 'reason':'no longer retained'})
    return {'version':1, 'snapshot':snapshot, 'production_modified':False,
            'actions':actions, 'rejected':rejected}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current-docs', required=True, type=Path)
    parser.add_argument('--history-docs', action='append', default=[], type=Path)
    parser.add_argument('--history-id', action='append', default=[])
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    current = args.current_docs.resolve()
    histories = [path.resolve() for path in args.history_docs]
    out = args.out.resolve()
    forbidden = [Path(__file__).resolve().parents[1], current, *histories]
    if any(out.is_relative_to(root) for root in forbidden):
        parser.error('plan output must be outside checkout and input snapshots')
    report = plan(current, histories, set(args.history_id))
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'snapshot':report['snapshot'], 'actions':len(report['actions']),
                      'rejected':report['rejected'], 'production_modified':False}))


if __name__ == '__main__':
    main()

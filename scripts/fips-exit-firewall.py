"""fw4 fixture continuity only: no guard readiness or semantic firewall proof."""
import copy
import json


def _unique_object(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('duplicate fw4 JSON attribute: ' + key)
        obj[key] = value
    return obj


def _normalized(snapshot):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('nftables'), list):
        raise ValueError('fw4 inventory required')
    tables = []
    for item in snapshot['nftables']:
        if not isinstance(item, dict) or len(item) != 1:
            raise ValueError('fw4 object required')
        kind, obj = next(iter(item.items()))
        if not isinstance(obj, dict):
            raise ValueError('fw4 object body required')
        if kind == 'table':
            tables.append(obj)
    if len(tables) != 1 or tables[0].get('family') != 'inet' or tables[0].get('name') != 'fw4':
        raise ValueError('unique inet fw4 table required')
    result = copy.deepcopy(snapshot)
    items = result['nftables']
    result['nftables'] = [item for item in items if set(item) != {'metainfo'}]
    for item in result['nftables']:
        for kind, obj in item.items():
            # Only object handles are identity metadata; never strip nested meta
            # expressions, comments, set elements or unknown attributes.
            if kind in ('table', 'chain', 'rule', 'set', 'map', 'counter'):
                obj.pop('handle', None)
            counters = [obj] if kind == 'counter' else []
            if kind == 'rule':
                counters += [expr['counter'] for expr in obj.get('expr', [])
                             if isinstance(expr.get('counter'), dict)]
            for counter in counters:
                for key in ('packets', 'bytes'):
                    if key in counter:
                        counter[key] = 0
    return result


def verify_fw4_unchanged(installed, current):
    """Exact fixture continuity except counters/metainfo/object handles.

    This does not validate the installed guard's semantics or grant readiness.
    List order and all other fields are significant, including unknown fields.
    """
    if _normalized(installed) != _normalized(current):
        raise ValueError('fw4 fixture changed')


def query_fw4(runner):
    """Capture the actual consumer table with a bounded, checked query."""
    result = runner.ns('consumer', ['nft', '-j', 'list', 'table', 'inet', 'fw4'],
                       timeout=5, check=True)
    result.check_returncode()
    snapshot = json.loads(result.stdout, object_pairs_hook=_unique_object)
    _normalized(snapshot)
    return snapshot

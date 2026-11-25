"""Pure, lane-specific route parser, NOT a RecoveryEvidence collector or executor.

Caller must supply the complete successful `ip -j -4 route show table 200`
stdout and its trusted query table. JSON alone cannot prove completeness,
namespace identity, freshness, or ownership. No Verified/Ownership attestations
are produced. query_routes reads through the caller's namespace runner;
no function here applies recovery operations.
"""
import json


def query_routes(runner):
    """Read the lane's private table, refusing command or inventory failures."""
    result = runner.ns('consumer',
                       ['ip', '-j', '-4', 'route', 'show', 'table', '200'],
                       timeout=5, check=True)
    result.check_returncode()
    return parse_routes(result.stdout, query_table=200)


def query_kernel_state(runner):
    """Capture bounded raw kernel observations, NOT recovery attestations.

    The caller must retain its private namespace ownership throughout collection.
    Only routes are semantically validated here; the other inventories are raw
    inputs for future exact installed-plan/identity verification. Never pass this
    observation bundle to BuildRecoveryPlan as RecoveryEvidence.
    """
    commands = {
        'links': ['ip', '-j', '-d', 'link', 'show'],
        'addresses': ['ip', '-j', 'address', 'show'],
        'tunnels': ['ip', '-j', '-6', 'tunnel', 'show'],
        'rules': ['ip', '-j', '-4', 'rule', 'show'],
        'routes': ['ip', '-j', '-4', 'route', 'show', 'table', '200'],
    }
    raw = {}
    for name, command in commands.items():
        result = runner.ns('consumer', command, timeout=5, check=True)
        result.check_returncode()
        raw[name] = result.stdout
    return {'scope': 'synthetic-kernel-fixture-observation-only', 'raw': raw,
            'routes': parse_routes(raw['routes'], query_table=200)}


def verify_fixture_baseline(observation, payload):
    """Check fixture tunnel identity against renderer output; not guard proof."""
    def interface(section):
        items = json.loads(observation['raw'][section], object_pairs_hook=_unique_object)
        matches = [item for item in items if item.get('ifname') == 'exit-test']
        if len(matches) != 1:
            raise ValueError('unique fixture tunnel required')
        return matches[0]

    link = interface('links')
    addr = interface('addresses')
    info = {'info_kind': 'ip6tnl', 'info_data': {
        'proto': 'ipip6', 'remote': payload['peer_outer'], 'local': payload['outer'],
        'link': 'mesh-test', 'ttl': 64, 'ip6_tnl_f_ign_encap_limit': True,
        'tclass': '0x00', 'flowlabel': '0x00000'}}
    if (type(link.get('ifindex')) is not int or link['ifindex'] <= 0
            or link.get('mtu') != 1240 or link.get('link') != 'mesh-test'
            or link.get('linkinfo') != info
            or link.get('flags') != ['POINTOPOINT', 'NOARP', 'UP', 'LOWER_UP']):
        raise ValueError('fixture tunnel differs from renderer plan')
    expected = [{'family': 'inet', 'local': payload['inner'], 'prefixlen': 32,
                 'scope': 'global', 'label': 'exit-test',
                 'valid_life_time': 4294967295, 'preferred_life_time': 4294967295}]
    if addr.get('ifindex') != link['ifindex'] or addr.get('addr_info') != expected:
        raise ValueError('fixture tunnel address differs from renderer plan')
    rules = json.loads(observation['raw']['rules'], object_pairs_hook=_unique_object)
    if rules != [{'priority': 0, 'src': 'all', 'table': 'local'},
                 {'priority': 10000, 'src': 'all', 'iif': 'guest-test', 'table': '200'},
                 {'priority': 32766, 'src': 'all', 'table': 'main'},
                 {'priority': 32767, 'src': 'all', 'table': 'default'}]:
        raise ValueError('fixture policy rules differ from plan')
    if len(parse_routes(observation['raw']['routes'], query_table=200)) != 2:
        raise ValueError('installed fixture must have both defaults')


def verify_inventory_unchanged(installed, current):
    """Reject drift from a caller-trusted installed fixture snapshot.

    Equality does NOT establish that the baseline was correct or owned. This
    is only a continuity check, not full RecoveryEvidence or permission to apply.
    Unknown changes (including benign kernel transitions) deliberately refuse.
    """
    for section in ('links', 'addresses', 'rules', 'tunnels'):
        before = json.loads(installed['raw'][section], object_pairs_hook=_unique_object)
        after = json.loads(current['raw'][section], object_pairs_hook=_unique_object)
        if not isinstance(before, list) or not before or before != after:
            raise ValueError('inventory changed or incomplete: ' + section)
    baseline = parse_routes(installed['raw']['routes'], query_table=200)
    if len(baseline) != 2:
        raise ValueError('installed fixture must have both defaults')
    return parse_routes(current['raw']['routes'], query_table=200)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON attribute: ' + key)
        result[key] = value
    return result


def parse_routes(raw, *, query_table):
    """Return Go RecoveryRoute-shaped dicts, or refuse the entire inventory.

    Only the two observed/expected iproute2 shapes below are supported; new
    attributes or output variants require explicit review, never filtering.
    """
    if type(query_table) is not int or query_table != 200:
        raise ValueError('trusted IPv4 table-200 query context required')
    routes = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(routes, list):
        raise ValueError('complete route inventory must be a JSON array')
    safety = dict(type='unreachable', dst='default', protocol='static',
                  metric=42760, flags=[])
    tunnel = dict(dst='default', dev='exit-test', protocol='static',
                  scope='link', metric=100, flags=[])
    result = []
    seen = set()
    for route in routes:
        if not isinstance(route, dict) or type(route.get('metric')) is not int:
            raise ValueError('route object with integer metric required')
        if route == safety:
            kind, device, metric = 'unreachable', '', 42760
        elif route == tunnel:
            kind, device, metric = 'unicast', 'exit-test', 100
        else:
            raise ValueError('foreign or uncertain route')
        if kind in seen:
            raise ValueError('duplicate route')
        seen.add(kind)
        result.append(dict(Type=kind, Destination='default', Device=device,
                           Protocol='static', Table=query_table, Metric=metric, Exact=True))
    if 'unreachable' not in seen:
        raise ValueError('unreachable safety default missing')
    return result

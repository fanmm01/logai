"""Deterministic playoff graphs, draws and group qualification (no battle engine)."""
import copy
import itertools
import random


FORMATS = {
    'playin12': ('12 进 8（现有）', 12),
    'groups16': ('16 进 8 · 四队双循环六轮', 16),
    'hybrid12': ('12 队 · 前八双败、后四单败', 12),
    'double16': ('16 强双败', 16),
    'single16': ('16 强单败', 16),
    'playin18': ('前十四直进 · 15–18 名小组附加赛', 18),
    'groups18': ('18 队 · 三组六队双循环十轮', 18),
    'nba16': ('仿 NBA · 两半区前六直进＋附加赛', 16),
}
DEFAULT_CONFIG = dict(format='playin12', draw='weighted', display='graph', reset_final=False, round_bo={})


def validate_config(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_CONFIG):
        raise ValueError('季后赛配置字段无效')
    if not isinstance(value['format'], str) or value['format'] not in FORMATS:
        raise ValueError('请选择有效的季后赛赛制')
    if value['draw'] not in ('random', 'tiered', 'ranked', 'weighted') or value['display'] not in ('graph', 'list'):
        raise ValueError('季后赛抽签或显示方式无效')
    if value['draw'] == 'weighted' and value['format'] != 'playin12':
        raise ValueError('现有加权抽签仅适用于 12 进 8')
    if type(value['reset_final']) is not bool or not isinstance(value['round_bo'], dict):
        raise ValueError('季后赛配置无效')
    allowed = set()
    for fmt in FORMATS:
        for s in build(list(range(18)), 0, {}, dict(DEFAULT_CONFIG, format=fmt, draw='ranked', reset_final=True))['series']:
            allowed.add(fmt + '/' + s['round'])
    allowed.update('nba16/' + r for r in ('PIA', 'PIB', 'R16', 'QF', 'SF', 'BRONZE', 'FINAL'))
    if any(k not in allowed or type(n) is not int or n < 1 or n % 2 != 1 for k, n in value['round_bo'].items()):
        raise ValueError('逐轮 BO 必须为正奇数且属于有效轮次')
    return copy.deepcopy(value)


def seed_order(size):
    order = [0, 1]
    while len(order) < size:
        order = [v for x in order for v in (x, len(order) * 2 - 1 - x)]
    return order


def draw_slots(entrants, mode, rng):
    entrants = list(entrants)
    if mode == 'random':
        rng.shuffle(entrants)
        return entrants
    if mode == 'tiered':
        half = len(entrants) // 2
        high, low = entrants[:half], entrants[half:]
        rng.shuffle(high)
        rng.shuffle(low)
        return [v for pair in zip(high, low) for v in pair]
    return [entrants[i] for i in seed_order(len(entrants))]


def build(ranked, seed, settings, config):
    """Return the entire graph, including deferred qualifier slots and reset final."""
    fmt, mode = config['format'], config['draw']
    rng = random.Random(seed + 701)
    series, groups = [], []
    nba_halves = []
    first = settings.get('BO_N_PLAYIN', 5)
    other = settings.get('BO_N_QF', 7)
    semi = settings.get('BO_N_SF', 7)
    final = settings.get('BO_N_FINAL', 7)

    def add(identifier, round_id, stage, a, b, category='other', **extra):
        n = config['round_bo'].get(fmt + '/' + round_id, dict(first=first, other=other, semi=semi, final=final)[category])
        series.append(dict(id=identifier, round=round_id, stage=stage, a=a, b=b, best_of=n,
                           score=[0, 0], games=[], winner=None, loser=None, **extra))
        return 'W:' + identifier

    def single(entrants, opening=False):
        size = len(entrants)
        slots = draw_slots(entrants, mode, rng)
        while size > 2:
            prefix, title = {16: ('R16', '十六进八'), 8: ('QF', '八进四'), 4: ('SF', '半决赛')}[size]
            category = 'semi' if size == 4 else 'first' if opening else 'other'
            slots = [add(f'{prefix}{i+1}', prefix, title, slots[2*i], slots[2*i+1], category) for i in range(size//2)]
            if size == 4:
                add('BRONZE', 'BRONZE', '铜牌赛', 'L:SF1', 'L:SF2', 'final')
            size //= 2
            opening = False
        add('FINAL', 'FINAL', '总决赛', *slots, 'final')

    def pool(entrants, count):
        size = len(entrants) // count
        members = [[] for _ in range(count)]
        entrants = list(entrants)
        if mode == 'random':
            rng.shuffle(entrants)
            members = [entrants[i*size:(i+1)*size] for i in range(count)]
        else:
            for tier in range(size):
                chunk = entrants[tier*count:(tier+1)*count]
                if mode == 'tiered':
                    rng.shuffle(chunk)
                elif tier % 2:
                    chunk.reverse()
                for i, team in enumerate(chunk):
                    members[i].append(team)
        for i, teams in enumerate(members):
            gid = chr(65 + i)
            groups.append(dict(id=gid, teams=teams))
            rotation = list(teams)
            fixtures = []
            for _ in range(size-1):
                fixtures.append([(rotation[j], rotation[-1-j]) for j in range(size//2)])
                rotation = [rotation[0], rotation[-1], *rotation[1:-1]]
            for r in range(2*(size-1)):
                for j, pair in enumerate(fixtures[r % (size-1)]):
                    if r >= size-1:
                        pair = pair[::-1]
                    add(f'{gid}{r+1}-{j+1}', f'G{r+1}', f'小组赛第 {r+1} 轮', *pair,
                        'first' if r == 0 else 'other', group=gid)

    if fmt == 'playin12':
        top, rest = list(ranked[:4]), list(ranked[4:12])
        if mode == 'weighted':
            remaining = list(range(8))
            for i, team in enumerate(top):
                pair = []
                for _ in range(2):
                    chosen = rng.choices(remaining, weights=[settings.get('KNOCKOUT_DRAW_WEIGHTS', [[1]*4]*8)[c][i] for c in remaining])[0]
                    remaining.remove(chosen)
                    pair.append(rest[chosen])
                add(f'PI{i+1}', 'PI', '附加赛', *pair, 'first')
                add(f'QF{i+1}', 'QF', '八进四', team, f'W:PI{i+1}')
            pairs = [('W:QF1', 'W:QF4'), ('W:QF2', 'W:QF3')]
        else:
            pi = draw_slots(rest, mode, rng)
            qualifiers = [add(f'PI{i+1}', 'PI', '附加赛', pi[i*2], pi[i*2+1], 'first') for i in range(4)]
            if mode == 'random':
                rng.shuffle(top)
            if mode in ('random', 'tiered'):
                rng.shuffle(qualifiers)
            for i in range(4):
                add(f'QF{i+1}', 'QF', '八进四', top[i], qualifiers[3-i])
            pairs = [('W:QF1', 'W:QF4'), ('W:QF2', 'W:QF3')]
        for i, pair in enumerate(pairs):
            add(f'SF{i+1}', 'SF', '半决赛', *pair, 'semi')
        add('BRONZE', 'BRONZE', '铜牌赛', 'L:SF1', 'L:SF2', 'final')
        add('FINAL', 'FINAL', '总决赛', 'W:SF1', 'W:SF2', 'final')
    elif fmt == 'nba16':
        field = list(ranked)
        if mode == 'random':
            rng.shuffle(field)
            middle = (len(field)+1)//2
            halves = [field[:middle], field[middle:]]
        elif mode == 'tiered':
            halves = [[], []]
            for start in range(0, len(field), 2):
                tier = field[start:start+2]
                rng.shuffle(tier)
                halves[0].append(tier[0])
                if len(tier) == 2:
                    halves[1].append(tier[1])
        else:
            halves = [field[::2], field[1::2]]
        for side, entrants in enumerate(halves):
            gid = 'A' if side == 0 else 'B'
            nba_halves.append(list(entrants))
            playin = entrants[6:10]
            if len(playin) >= 3:
                add(f'PI{gid}78', 'PI' + gid, f'{gid} 半区 7/8 附加赛', playin[0], playin[1], 'first', conference=gid)
            if len(playin) == 3:
                add(f'PI{gid}9', 'PI' + gid, f'{gid} 半区第 9 名挑战赛', playin[2], f'L:PI{gid}78', 'other', conference=gid)
            elif len(playin) >= 4:
                add(f'PI{gid}910', 'PI' + gid, f'{gid} 半区 9/10 附加赛', playin[2], playin[3], 'other', conference=gid)
                add(f'PI{gid}Q', 'PI' + gid, f'{gid} 半区晋级赛', f'L:PI{gid}78', f'W:PI{gid}910', 'other', conference=gid)
        entrants = []
        for gid in ('A', 'B'):
            half = halves[0 if gid == 'A' else 1]
            entrants.extend(half[:6])
            entrants.extend([f'N:{gid}7', f'N:{gid}8'])
        single(entrants, True)
    elif fmt in ('single16', 'groups16', 'playin18', 'groups18'):
        if fmt == 'single16':
            single(ranked[:16], True)
        else:
            pool(ranked[14:18] if fmt == 'playin18' else ranked[:16 if fmt == 'groups16' else 18],
                 1 if fmt == 'playin18' else 4 if fmt == 'groups16' else 3)
            # Draw qualifying teams only after all groups finish. The graph stays stable.
            single([f'D:{i}' for i in range(16 if fmt == 'playin18' else 8)])
    else:
        size = 8 if fmt == 'hybrid12' else 16
        slots = draw_slots(ranked[:size], mode, rng)
        upper = [add(f'U1-{i+1}', 'U1', '胜者组第 1 轮', slots[2*i], slots[2*i+1], 'first') for i in range(size//2)]
        losers = [s.replace('W:', 'L:') for s in upper]
        if fmt == 'hybrid12':
            low = list(ranked[8:12])
            if mode in ('random', 'tiered'):
                rng.shuffle(low)
            lower = [add(f'L0-{i+1}', 'L0', '败者组入围轮', losers[i], low[3-i], 'first') for i in range(4)]
        else:
            lower = losers
        ur, lr = 1, 0
        while len(upper) > 1:
            # Reduce lower bracket, then receive the next upper round's losers.
            lr += 1
            lower = [add(f'L{lr}-{i+1}', f'L{lr}', f'败者组第 {lr} 轮', lower[2*i], lower[2*i+1]) for i in range(len(lower)//2)]
            ur += 1
            upper = [add(f'U{ur}-{i+1}', f'U{ur}', '胜者组决赛' if len(upper) == 2 else f'胜者组第 {ur} 轮',
                         upper[2*i], upper[2*i+1], 'semi' if len(upper) == 2 else 'other') for i in range(len(upper)//2)]
            drops = [s.replace('W:', 'L:') for s in upper][::-1]
            lr += 1
            lower = [add(f'L{lr}-{i+1}', f'L{lr}', '败者组决赛' if len(lower) == 1 else f'败者组第 {lr} 轮',
                         lower[i], drops[i], 'semi' if len(lower) == 1 else 'other') for i in range(len(lower))]
        add('FINAL', 'FINAL', '总决赛', upper[0], lower[0], 'final')
        if config['reset_final']:
            add('RESET', 'RESET', '重置总决赛', 'W:FINAL', 'L:FINAL', 'final', conditional=True)
    # Stable round grouping also makes round simulation and graph columns agree.
    order = {r: i for i, r in enumerate(dict.fromkeys(s['round'] for s in series))}
    series.sort(key=lambda s: order[s['round']])
    return dict(series=series, groups=groups, nba_halves=nba_halves)


def group_tables(t):
    """Series wins, game differential, game wins, then original regular-season rank."""
    rank = {row['id']: i for i, row in enumerate(sorted(t['standings'], key=lambda x: (-x['points'], -x['wins'], x['id'])))}
    tables = []
    for group in t.get('groups', []):
        rows = {team: dict(id=team, wins=0, played=0, game_wins=0, game_losses=0) for team in group['teams']}
        matches = [s for s in t['series'] if s.get('group') == group['id']]
        for s in matches:
            for side, key in enumerate(('a', 'b')):
                rows[s[key]]['game_wins'] += s['score'][side]
                rows[s[key]]['game_losses'] += s['score'][1-side]
                if s['winner'] is not None:
                    rows[s[key]]['played'] += 1
            if s['winner'] is not None:
                rows[s['winner']]['wins'] += 1
        def key(row):
            return (-row['wins'], -(row['game_wins']-row['game_losses']), -row['game_wins'], rank[row['id']])
        tables.append(dict(id=group['id'], rows=sorted(rows.values(), key=key), complete=all(s['winner'] is not None for s in matches)))
    return tables


def qualifiers(t):
    fmt = t['playoff']['format']
    if fmt == 'nba16':
        qualified = []
        for gid, half in zip(('A', 'B'), (t['nba_halves'][0], t['nba_halves'][1])):
            playin = half[6:10]
            qualified.extend(half[:6])
            if len(playin) >= 3:
                ids = [f'PI{gid}78']
                if len(playin) == 3:
                    ids.append(f'PI{gid}9')
                elif len(playin) >= 4:
                    ids.extend((f'PI{gid}910', f'PI{gid}Q'))
                matches = [next(s for s in t['series'] if s['id'] == identifier) for identifier in ids]
                if any(s['winner'] is None for s in matches):
                    return None
                if len(playin) == 2:
                    qualified.extend(playin)
                elif len(playin) == 3:
                    qualified.extend([matches[0]['winner'], matches[1]['winner']])
                else:
                    qualified.extend([matches[0]['winner'], matches[-1]['winner']])
            else:
                qualified.extend(playin)
        return qualified
    tables = group_tables(t)
    if not tables or not all(g['complete'] for g in tables):
        return None
    ranked = [row['id'] for row in sorted(t['standings'], key=lambda x: (-x['points'], -x['wins'], x['id']))]
    qualified = [row['id'] for g in tables for row in g['rows'][:2]]
    if fmt == 'groups18':
        thirds = [g['rows'][2] for g in tables]
        thirds.sort(key=lambda r: (-r['wins'], -(r['game_wins']-r['game_losses']), -r['game_wins'], ranked.index(r['id'])))
        qualified += [r['id'] for r in thirds[:2]]
    if fmt == 'playin18':
        qualified += ranked[:14]
    qualified.sort(key=ranked.index)
    if fmt in ('groups16', 'groups18'):
        # Keep seed positions and high/low tiers; permute opponents to avoid
        # rematches where the group distribution permits it.
        membership = {team: g['id'] for g in t['groups'] for team in g['teams']}
        first_round = next(s['round'] for s in t['series'] if not s.get('group'))
        pairs = [(int(s['a'][2:]), int(s['b'][2:])) for s in t['series'] if s['round'] == first_round]
        right = [b for _, b in pairs]
        opponents = [qualified[b] for b in right]
        best, conflicts = opponents, len(pairs) + 1
        for candidates in itertools.permutations(opponents):
            count = sum(membership[qualified[a]] == membership[team] for (a, _), team in zip(pairs, candidates))
            if count < conflicts:
                best, conflicts = candidates, count
            if not count:
                break
        for index, team in zip(right, best):
            qualified[index] = team
    return qualified


def participant(t, source):
    if isinstance(source, int):
        return source
    kind, identifier = source.split(':', 1)
    if kind == 'D':
        qualified = qualifiers(t)
        return None if qualified is None else qualified[int(identifier)]
    if kind == 'N':
        gid, place = identifier[0], int(identifier[1:])
        half = t['nba_halves'][0 if gid == 'A' else 1]
        if place <= 6:
            return half[place-1]
        playin = half[6:10]
        if len(playin) == 2:
            return playin[place-7]
        suffix = '78' if place == 7 else '9' if len(playin) == 3 else 'Q'
        return next(s for s in t['series'] if s['id'] == f'PI{gid}{suffix}')['winner']
    s = next(x for x in t['series'] if x['id'] == identifier)
    return s['winner' if kind == 'W' else 'loser']


def settle_reset(t):
    """The conditional reset is skipped when the upper champion wins the final."""
    reset = next((s for s in t['series'] if s.get('conditional')), None)
    if reset:
        final = next(s for s in t['series'] if s['id'] == 'FINAL')
        if final['winner'] is not None and final['winner'] == participant(t, final['a']):
            reset.update(skipped=True)


def finished(s):
    return s['winner'] is not None or s.get('skipped', False)

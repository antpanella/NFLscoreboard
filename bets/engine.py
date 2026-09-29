#!/usr/bin/env python3
"""Bet recommender engine for the Sunday Board.

Runs in GitHub Actions (the Odds API and ESPN are reachable from there, not from
Claude's sandbox or the work network). Standard library only.

Modes
  request   pull odds for every open request file in bets/requests/ (push-triggered)
  pull D    pull odds for ET date D (YYYY-MM-DD) and write candidates
  tick      every 15 min: refresh the schedule, morning game-line snapshot (line movement),
            closing lines for the card's plays (game lines and props), grade results
  grade     grade whatever in the ledger has finished
  parlay D id id ...   price a parlay from candidate ids (used by the pre-kickoff session)
  record D picks.json  write the day's card, ledger entries and latest.json from the session's choices
  late D scratch.json  record a late check: scratch card plays that no longer hold
  (request file {"mode": "latecheck", "date": D} re-prices the card's game-line plays: 3 credits)
  (request file {"mode": "extra", "date": D} adds count props to the day's pull: 5 credits a game)
  selftest  run the math against the saved coverage test in data/odds-test/ (no network)

Credits (free tier, 500/month): event list 0, game lines 3 per pull (whole slate),
props 4 per game (+5 for count props, primetime games only, while 150+ credits are left),
morning snapshot 3 per gameday, closing props 1 per market on the card per game.
Up to 10 named books count as one region.
"""
import json
import math
import os
import sys
import hashlib
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from statistics import NormalDist, median
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))          # bets/
REPO = os.path.dirname(HERE)
DATA = os.path.join(HERE, 'data')
REQS = os.path.join(HERE, 'requests')
LEDGER = os.path.join(HERE, 'ledger.json')
SUMMARY = os.path.join(HERE, 'summary.json')
QUOTA = os.path.join(DATA, 'quota.json')
SCHEDULE = os.path.join(DATA, 'schedule.json')

ET = ZoneInfo('America/New_York')
API = 'https://api.the-odds-api.com/v4/sports/americanfootball_nfl'
ESPN = 'https://site.web.api.espn.com/apis/site/v2/sports/football/nfl'   # site.api blocks GitHub's servers

# Ten books = one region's worth of credits. DraftKings is the book we bet;
# Pinnacle is the sharp reference; the rest feed the consensus fallback.
BOOKS = ['draftkings', 'pinnacle', 'fanduel', 'betmgm', 'williamhill_us',
         'betrivers', 'fanatics', 'betonlineag', 'bovada', 'lowvig']
LINE_MARKETS = ['h2h', 'spreads', 'totals']
PROP_MARKETS = ['player_pass_yds', 'player_rush_yds', 'player_reception_yds', 'player_anytime_td']
# Count props pulled only on request (mode "extra"): priced at Pinnacle's or the
# consensus's exact number, never moved to another number with a curve.
EXTRA_MARKETS = ['player_receptions', 'player_pass_tds', 'player_pass_completions',
                 'player_pass_attempts', 'player_rush_attempts']
PROP_SHORT = {'player_pass_yds': 'pass yds', 'player_rush_yds': 'rush yds',
              'player_reception_yds': 'rec yds', 'player_anytime_td': 'anytime TD',
              'player_receptions': 'receptions', 'player_pass_tds': 'pass TDs',
              'player_pass_completions': 'completions', 'player_pass_attempts': 'pass att',
              'player_rush_attempts': 'rush att'}
ESPN_STAT = {'player_pass_yds': 'passingYards', 'player_rush_yds': 'rushingYards',
             'player_reception_yds': 'receivingYards', 'player_receptions': 'receptions',
             'player_pass_tds': 'passingTouchdowns', 'player_pass_completions': 'completions',
             'player_pass_attempts': 'passingAttempts', 'player_rush_attempts': 'rushingAttempts'}

# Spread of outcomes used only to move a fair price a short way to a different number.
SIGMA_SPREAD = 13.5          # NFL margin vs spread, points
SIGMA_TOTAL = 13.0           # NFL total vs posted total, points
SIGMA_PROP = {'player_pass_yds': 0.27, 'player_rush_yds': 0.55, 'player_reception_yds': 0.65}  # x line
KEY_NUMBERS = (3, 7)
MAX_SHIFT_LINES = 1.5        # points
MAX_SHIFT_PROPS = 0.15       # fraction of the line
PROP_ADJUST_MIN_LINE = 20    # below this a yardage line is too lumpy to move with a normal curve
CONSENSUS_MIN_BOOKS = 3
CREDIT_RESERVE = 15          # never spend below this
LATE_MIN_CREDITS = 100       # the late check is a nice-to-have: it never eats into the core budget
CLOSE_MIN_CREDITS = 40       # nor does the closing-line snapshot
CLOSE_WINDOW_MIN = 45        # closing snapshot when kickoff is this close (GitHub's 15-min cron fires 13-31 min apart)
EXTRA_MIN_CREDITS = 150      # count props ride along on primetime pulls only above this
EXTRA_FROM_HOUR_ET = 19      # "primetime": kickoff at or after 7 PM ET
OPENER_MIN_CREDITS = 60      # the morning game-line snapshot (line movement)
OPENER_HOURS = 10            # take it once the day's first kickoff is this close...
OPENER_MIN_LEAD_H = 4        # ...and still at least this far away (before the pre-kickoff pull)
MOVE_TAG_PTS = 0.015         # fair-probability move that counts as Pinnacle moving

TEAMS = {
    'Arizona Cardinals': 'ARI', 'Atlanta Falcons': 'ATL', 'Baltimore Ravens': 'BAL',
    'Buffalo Bills': 'BUF', 'Carolina Panthers': 'CAR', 'Chicago Bears': 'CHI',
    'Cincinnati Bengals': 'CIN', 'Cleveland Browns': 'CLE', 'Dallas Cowboys': 'DAL',
    'Denver Broncos': 'DEN', 'Detroit Lions': 'DET', 'Green Bay Packers': 'GB',
    'Houston Texans': 'HOU', 'Indianapolis Colts': 'IND', 'Jacksonville Jaguars': 'JAX',
    'Kansas City Chiefs': 'KC', 'Las Vegas Raiders': 'LV', 'Los Angeles Chargers': 'LAC',
    'Los Angeles Rams': 'LAR', 'Miami Dolphins': 'MIA', 'Minnesota Vikings': 'MIN',
    'New England Patriots': 'NE', 'New Orleans Saints': 'NO', 'New York Giants': 'NYG',
    'New York Jets': 'NYJ', 'Philadelphia Eagles': 'PHI', 'Pittsburgh Steelers': 'PIT',
    'San Francisco 49ers': 'SF', 'Seattle Seahawks': 'SEA', 'Tampa Bay Buccaneers': 'TB',
    'Tennessee Titans': 'TEN', 'Washington Commanders': 'WSH',
}

N = NormalDist()


# ---------------------------------------------------------------- basics
def now():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse(s):
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def et_date(s):
    return parse(s).astimezone(ET).strftime('%Y-%m-%d')


def et_clock(s):
    return parse(s).astimezone(ET).strftime('%a %-I:%M %p ET')


def abbr(team):
    return TEAMS.get(team, team)


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save(path, obj, compact=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        if compact:
            json.dump(obj, f, separators=(',', ':'))
        else:
            json.dump(obj, f, indent=1)
        f.write('\n')


def day_dir(date):
    return os.path.join(DATA, date)


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- network
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/128.0 Safari/537.36')


def http_json(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()), dict(r.headers)


def odds(path, **params):
    """Odds API call. Records the quota counters; never prints the key."""
    key = os.environ.get('ODDS_API_KEY', '')
    if not key:
        raise SystemExit('ODDS_API_KEY is not set')
    params['apiKey'] = key
    url = API + path + '?' + urllib.parse.urlencode(params)
    try:
        body, headers = http_json(url)
    except urllib.error.HTTPError as e:
        raise SystemExit(f'Odds API {path} returned HTTP {e.code}: {e.read()[:200]!r}')
    h = {k.lower(): v for k, v in headers.items()}
    if 'x-requests-remaining' in h:
        q = {'remaining': int(float(h['x-requests-remaining'])),
             'used': int(float(h.get('x-requests-used', 0))),
             'last_cost': int(float(h.get('x-requests-last', 0))),
             'checked_at': iso(now())}
        old = load(QUOTA) or {}
        if (old.get('remaining'), old.get('used')) != (q['remaining'], q['used']):
            save(QUOTA, q)                                 # only when credits actually moved
        log(f'  {path}: cost {q["last_cost"]}, {q["remaining"]} credits left')
    return body


def remaining():
    return (load(QUOTA) or {}).get('remaining', 500)


# ---------------------------------------------------------------- odds math
def dec(a):
    return 1 + a / 100 if a > 0 else 1 + 100 / -a


def to_american(d):
    if d >= 2:
        return round((d - 1) * 100)
    return round(-100 / (d - 1))


def fair_american(p):
    if not p or p <= 0 or p >= 1:
        return None
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def Phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def Phi_inv(p):
    return N.inv_cdf(min(max(p, 1e-6), 1 - 1e-6))


# A selection is (market, subject, side, point).
#   h2h:      (h2h, team, 'win', None)
#   spreads:  (spreads, team, 'cover', point)
#   totals:   (totals, 'game', Over|Under, point)
#   props:    (player_*, player, Over|Under, point)
#   anytime:  (player_anytime_td, player, Yes|No, None)
def index_book(book, event):
    out = {}
    for m in book.get('markets', []):
        mk = m['key']
        for o in m.get('outcomes', []):
            pt = o.get('point')
            if mk == 'h2h':
                key = ('h2h', o['name'], 'win', None)
            elif mk == 'spreads':
                key = ('spreads', o['name'], 'cover', pt)
            elif mk == 'totals':
                key = ('totals', 'game', o['name'], pt)
            elif mk == 'player_anytime_td':
                key = (mk, o.get('description') or o['name'], o['name'], None)
            else:
                key = (mk, o.get('description', ''), o['name'], pt)
            out[key] = o['price']
    return out


def book_updated(book):
    ups = {m['key']: m.get('last_update') for m in book.get('markets', [])}
    return ups, book.get('last_update')


def opposite(key, event):
    mk, subj, side, pt = key
    if mk == 'h2h':
        other = event['away_team'] if subj == event['home_team'] else event['home_team']
        return (mk, other, side, None)
    if mk == 'spreads':
        other = event['away_team'] if subj == event['home_team'] else event['home_team']
        return (mk, other, side, -pt if pt is not None else None)
    flip = {'Over': 'Under', 'Under': 'Over', 'Yes': 'No', 'No': 'Yes'}
    return (mk, subj, flip.get(side, side), pt)


def devig(idx, key, event):
    """No-vig probability of key from one book, if it has both sides at this number."""
    a = idx.get(key)
    b = idx.get(opposite(key, event))
    if a is None or b is None:
        return None, None
    ia, ib = 1 / dec(a), 1 / dec(b)
    return ia / (ia + ib), ia + ib - 1


def crosses_key(q, d):
    lo, hi = min(q, d), max(q, d)
    return any(lo <= s <= hi for k in KEY_NUMBERS for s in (k, -k))


def shift(mk, side, q, p_at_q, d):
    """Move a fair probability from number q to number d with a normal model.
    Returns None where the move is too far or crosses a key number."""
    if q == d:
        return p_at_q
    if mk == 'spreads':
        if abs(d - q) > MAX_SHIFT_LINES or crosses_key(q, d):
            return None
        mu = SIGMA_SPREAD * Phi_inv(p_at_q) - q           # team margin
        return Phi((mu + d) / SIGMA_SPREAD)
    if mk == 'totals':
        if abs(d - q) > MAX_SHIFT_LINES:
            return None
        sigma = SIGMA_TOTAL
    elif mk in SIGMA_PROP:
        if min(q, d) < PROP_ADJUST_MIN_LINE or abs(d - q) > MAX_SHIFT_PROPS * q:
            return None
        sigma = SIGMA_PROP[mk] * q
    else:
        return None
    p_over = p_at_q if side == 'Over' else 1 - p_at_q
    mu = q + sigma * Phi_inv(p_over)
    p_over_d = 1 - Phi((d - mu) / sigma)
    return p_over_d if side == 'Over' else 1 - p_over_d


def pin_adjusted(pin_idx, key, event):
    """Pinnacle's fair price moved from its nearest number to ours."""
    mk, subj, side, pt = key
    if pt is None:
        return None
    best = None
    for k in pin_idx:
        if k[0] == mk and k[1] == subj and k[2] == side and k[3] is not None and k[3] != pt:
            p, vig = devig(pin_idx, k, event)
            if p is None:
                continue
            if best is None or abs(k[3] - pt) < abs(best[0] - pt):
                best = (k[3], p, vig)
    if not best:
        return None
    q, p, vig = best
    p2 = shift(mk, side, q, p, pt)
    if p2 is None:
        return None
    return p2, vig, q


def fair_for(key, event, books):
    """Best available fair probability for a DraftKings selection.
    Order: Pinnacle at the same number, Pinnacle moved to our number,
    consensus of other books at the same number."""
    pin = books.get('pinnacle')
    res = {'source': None}
    if pin:
        p, vig = devig(pin, key, event)
        if p is not None:
            res.update(source='pinnacle', p=p, vig=vig)
        else:
            adj = pin_adjusted(pin, key, event)
            if adj:
                res.update(source='pinnacle-adjusted', p=adj[0], vig=adj[1], from_point=adj[2])
    ps = []
    for name, idx in books.items():
        if name in ('draftkings', 'pinnacle'):
            continue
        p, _ = devig(idx, key, event)
        if p is not None:
            ps.append(p)
    if len(ps) >= CONSENSUS_MIN_BOOKS:
        res['consensus_p'] = median(ps)
        res['consensus_n'] = len(ps)
        if res['source'] is None:
            res.update(source='consensus', p=median(ps))
    return res


def label(key, event):
    mk, subj, side, pt = key
    if mk == 'h2h':
        return f'{abbr(subj)} ML'
    if mk == 'spreads':
        return f'{abbr(subj)} {pt:+g}'
    if mk == 'totals':
        return f'{abbr(event["away_team"])}@{abbr(event["home_team"])} {side.lower()} {pt:g}'
    if mk == 'player_anytime_td':
        return f'{subj} anytime TD' if side == 'Yes' else f'{subj} no TD'
    return f'{subj} {side.lower()} {pt:g} {PROP_SHORT.get(mk, mk)}'


def cand_id(event_id, key):
    raw = json.dumps([event_id, list(key)])
    return hashlib.sha1(raw.encode()).hexdigest()[:8]


def evaluate(event, bookmakers):
    """Every DraftKings selection in this event with its fair price and EV."""
    books = {b['key']: index_book(b, event) for b in bookmakers}
    stamps = {b['key']: book_updated(b) for b in bookmakers}
    dk = books.get('draftkings')
    if not dk:
        return []
    out = []
    for key, price in dk.items():
        if key[0] == 'player_anytime_td' and key[2] != 'Yes':
            continue
        f = fair_for(key, event, books)
        if f['source'] is None:
            continue
        p = f['p']
        ev = p * dec(price) - 1
        mk = key[0]
        c = {
            'id': cand_id(event['id'], key),
            'event_id': event['id'],
            'game': f'{abbr(event["away_team"])} @ {abbr(event["home_team"])}',
            'home': event['home_team'], 'away': event['away_team'],
            'commence': event['commence_time'],
            'market': mk, 'subject': key[1], 'side': key[2], 'point': key[3],
            'label': label(key, event),
            'dk_price': price,
            'fair_prob': round(p, 4),
            'fair_price': fair_american(p),
            'ev': round(ev, 4),
            'source': f['source'],
            'dk_updated': stamps['draftkings'][0].get(mk),
        }
        if 'from_point' in f:
            c['from_point'] = f['from_point']
        if f.get('vig') is not None:
            c['pin_vig'] = round(f['vig'], 4)
        if 'pinnacle' in stamps:
            c['pin_updated'] = stamps['pinnacle'][0].get(mk)
        if 'consensus_p' in f:
            c['consensus_prob'] = round(f['consensus_p'], 4)
            c['consensus_books'] = f['consensus_n']
            if f['source'] != 'consensus' and abs(f['consensus_p'] - p) > 0.04:
                c['flag'] = 'pinnacle and consensus disagree by >4 pts'
        out.append(c)
    return out


# ---------------------------------------------------------------- schedule
def fetch_events():
    evs = odds('/events')                                  # free
    return sorted(evs, key=lambda e: e['commence_time'])


def write_schedule(evs):
    sched = {'updated': iso(now()), 'events': [
        {'id': e['id'], 'commence': e['commence_time'], 'et_date': et_date(e['commence_time']),
         'kickoff_et': et_clock(e['commence_time']),
         'home': e['home_team'], 'away': e['away_team'],
         'game': f'{abbr(e["away_team"])} @ {abbr(e["home_team"])}'} for e in evs]}
    old = load(SCHEDULE) or {}
    if old.get('events') != sched['events']:
        save(SCHEDULE, sched)
        log(f'schedule: {len(evs)} games listed')
        return True
    return False


# ---------------------------------------------------------------- pull
def pull(date, evs=None):
    """Lines for the slate plus props for every game on ET date `date` that has
    not kicked off. Writes raw odds and candidates for the pre-kickoff session."""
    evs = evs if evs is not None else fetch_events()
    write_schedule(evs)
    t = now()
    games = [e for e in evs if et_date(e['commence_time']) == date and parse(e['commence_time']) > t]
    d = day_dir(date)
    if not games:
        log(f'pull {date}: no games left to pull')
        save(os.path.join(d, 'candidates.json'), {'date': date, 'pulled_at': iso(t),
             'note': 'no games on this date that have not kicked off', 'games': [], 'candidates': []})
        return
    if remaining() - 3 < CREDIT_RESERVE:
        raise SystemExit(f'Only {remaining()} credits left; not pulling.')
    ids = {e['id'] for e in games}
    lines = odds('/odds', bookmakers=','.join(BOOKS), markets=','.join(LINE_MARKETS),
                 oddsFormat='american')
    lines = [g for g in lines if g['id'] in ids]
    save(os.path.join(d, 'lines.json'), lines, compact=True)

    props = {}
    skipped = []
    first_props = os.path.join(d, 'props-first.json')
    if os.path.exists(os.path.join(d, 'props.json')) and not os.path.exists(first_props):
        os.replace(os.path.join(d, 'props.json'), first_props)   # the day's earliest props: movement reference
    for e in games:
        mks = list(PROP_MARKETS)
        if (parse(e['commence_time']).astimezone(ET).hour >= EXTRA_FROM_HOUR_ET
                and remaining() - len(mks) - len(EXTRA_MARKETS) >= EXTRA_MIN_CREDITS):
            mks += EXTRA_MARKETS                               # primetime: count props too
        if remaining() - len(mks) < CREDIT_RESERVE:
            skipped.append(e['id'])
            continue
        try:
            props[e['id']] = odds(f'/events/{e["id"]}/odds', bookmakers=','.join(BOOKS),
                                  markets=','.join(mks), oddsFormat='american')
        except SystemExit as err:
            log(f'  props failed for {e["id"]}: {err}')
            skipped.append(e['id'])
    save(os.path.join(d, 'props.json'), props, compact=True)
    build_candidates(date, games, lines, props, skipped, t)


def pull_extra(date, markets):
    """More prop markets for games not yet started, merged into the day's props;
    game lines are reused from the last pull. Costs one credit per market per game."""
    d = day_dir(date)
    lines = load(os.path.join(d, 'lines.json'), [])
    props = load(os.path.join(d, 'props.json'), {})
    if props and not os.path.exists(os.path.join(d, 'props-first.json')):
        save(os.path.join(d, 'props-first.json'), props, compact=True)
    t = now()
    games = [g for g in lines if parse(g['commence_time']) > t]
    skipped = []
    for e in games:
        if remaining() - len(markets) < CREDIT_RESERVE:
            skipped.append(e['id'])
            continue
        try:
            got = odds(f'/events/{e["id"]}/odds', bookmakers=','.join(BOOKS),
                       markets=','.join(markets), oddsFormat='american')
        except SystemExit as err:
            log(f'  extra props failed for {e["id"]}: {err}')
            skipped.append(e['id'])
            continue
        base = props.setdefault(e['id'], dict(got, bookmakers=[]))
        have = {b['key']: b for b in base.get('bookmakers', [])}
        for b in got.get('bookmakers', []):
            if b['key'] in have:
                old = [m for m in have[b['key']]['markets'] if m['key'] not in markets]
                have[b['key']]['markets'] = old + b.get('markets', [])
            else:
                base['bookmakers'].append(b)
    save(os.path.join(d, 'props.json'), props, compact=True)
    build_candidates(date, games, lines, props, skipped, t)


def reference_books(date):
    """The day's earliest prices, per event: the morning game-line snapshot (opener.json)
    and the first prop pull (props-first.json), as bookmaker lists."""
    d = day_dir(date)
    ref, when = {}, {}
    op = load(os.path.join(d, 'opener.json'), {})
    for g in op.get('games', []):
        ref.setdefault(g['id'], []).extend(g.get('bookmakers', []))
        when[g['id']] = op.get('captured_at')
    pf = load(os.path.join(d, 'props-first.json'), {})
    for eid, g in pf.items():
        ref.setdefault(eid, []).extend(g.get('bookmakers', []))
    return ref, when, op.get('captured_at')


def add_movement(date, games, cands):
    """Pinnacle's fair price for each selection earlier in the day, and how far it has
    moved since. Positive move = toward our side (Pinnacle now likes the bet more)."""
    ref, _, opened = reference_books(date)
    cur_lines = {g['id']: g for g in games}
    for c in cands:
        bms = ref.get(c['event_id'])
        if not bms:
            continue
        event = cur_lines.get(c['event_id']) or {'home_team': c['home'], 'away_team': c['away']}
        books = {}
        for b in bms:                                       # same book may appear twice (lines + props)
            books.setdefault(b['key'], {}).update(index_book(b, event))
        key = (c['market'], c['subject'], c['side'], c['point'])
        pin = books.get('pinnacle')
        if not pin:
            continue
        p0, _ = devig(pin, key, event)
        if p0 is None:
            adj = pin_adjusted(pin, key, event)
            p0 = adj[0] if adj else None
        if p0 is None:
            continue
        c['open_fair_prob'] = round(p0, 4)
        c['pin_move'] = round(c['fair_prob'] - p0, 4)
        dk0 = books.get('draftkings', {}).get(key)
        if dk0 is not None:
            c['open_dk_price'] = dk0
        if c['source'] != 'consensus' and abs(c['pin_move']) >= MOVE_TAG_PTS:
            c['move'] = 'chasing' if c['pin_move'] > 0 else 'fading'
        else:
            c['move'] = 'flat'


def build_candidates(date, games, lines, props, skipped, pulled_at):
    by_id = {g['id']: g for g in lines}
    cands, game_notes = [], []
    for e in games:
        bms = list(by_id.get(e['id'], {}).get('bookmakers', []))
        pr = props.get(e['id'])
        if pr:
            bms_props = {b['key']: b for b in pr.get('bookmakers', [])}
            merged = []
            keys = {b['key'] for b in bms} | set(bms_props)
            for k in keys:
                base = next((b for b in bms if b['key'] == k), {'key': k, 'markets': []})
                extra = bms_props.get(k, {'markets': []})
                merged.append({'key': k, 'last_update': base.get('last_update') or extra.get('last_update'),
                               'markets': base.get('markets', []) + extra.get('markets', [])})
            bms = merged
        c = evaluate(e, bms)
        cands += c
        have = {b['key'] for b in bms}
        game_notes.append({'event_id': e['id'], 'game': f'{abbr(e["away_team"])} @ {abbr(e["home_team"])}',
                           'kickoff_et': et_clock(e['commence_time']), 'commence': e['commence_time'],
                           'draftkings': 'draftkings' in have, 'pinnacle': 'pinnacle' in have,
                           'props_pulled': e['id'] in props, 'selections': len(c)})
    add_movement(date, games, cands)
    cands.sort(key=lambda c: -c['ev'])
    out = {'date': date, 'pulled_at': iso(pulled_at), 'credits_left': remaining(),
           'props_skipped': skipped, 'games': game_notes, 'candidates': cands}
    d = day_dir(date)
    save(os.path.join(d, 'candidates.json'), out)
    write_top(date, out)
    log(f'pull {date}: {len(games)} games, {len(cands)} DraftKings selections priced, '
        f'{sum(1 for c in cands if c["ev"] > 0)} above fair')


def write_top(date, out):
    lines = [f'# Candidates {date}', '',
             f'Pulled {out["pulled_at"]} · credits left {out["credits_left"]}', '',
             '## Games', '',
             '| game | kickoff | DK | Pinnacle | props | priced |', '|---|---|---|---|---|---|']
    for g in out['games']:
        lines.append(f'| {g["game"]} | {g["kickoff_et"]} | {"yes" if g["draftkings"] else "NO"} | '
                     f'{"yes" if g["pinnacle"] else "no"} | {"yes" if g["props_pulled"] else "skipped"} | '
                     f'{g["selections"]} |')
    lines += ['', '## DraftKings selections at or above fair (best first)', '',
              '| id | game | bet | DK | fair | EV | source | notes |', '|---|---|---|---|---|---|---|---|']
    for c in out['candidates']:
        if c['ev'] < 0:
            break
        notes = []
        if c.get('from_point') is not None:
            notes.append(f'moved from {c["from_point"]:g}')
        if c.get('consensus_prob') is not None:
            notes.append(f'consensus {c["consensus_prob"]*100:.1f}% ({c["consensus_books"]} books)')
        if c.get('flag'):
            notes.append(c['flag'])
        if c.get('dk_updated') and c.get('pin_updated'):
            notes.append(f'DK upd {c["dk_updated"][11:16]}Z, PIN upd {c["pin_updated"][11:16]}Z')
        if c.get('move'):
            mv = f'{c["move"].upper()}: PIN fair {c["open_fair_prob"]*100:.1f}% -> {c["fair_prob"]*100:.1f}%'
            if c.get('open_dk_price') is not None:
                mv += f', DK {c["open_dk_price"]:+d} -> {c["dk_price"]:+d}'
            notes.insert(0, mv)
        fp = c['fair_price']
        lines.append(f'| {c["id"]} | {c["game"]} | {c["label"]} | {c["dk_price"]:+d} | '
                     f'{fp:+d} ({c["fair_prob"]*100:.1f}%) | {c["ev"]*100:+.1f}% | {c["source"]} | '
                     f'{"; ".join(notes)} |')
    with open(os.path.join(day_dir(date), 'candidates-top.md'), 'w') as f:
        f.write('\n'.join(lines) + '\n')


def run_requests():
    """Pull for every request file the pre-kickoff session has pushed and not yet had filled."""
    if not os.path.isdir(REQS):
        return
    evs = None
    for name in sorted(os.listdir(REQS)):
        if not name.endswith('.json'):
            continue
        path = os.path.join(REQS, name)
        r = load(path, {})
        if r.get('fulfilled_at'):
            continue
        date = r.get('date') or name[:10]
        if r.get('mode') == 'probe':                           # which data sources answer from here
            lines_out = []
            for u in r.get('urls', []):
                try:
                    req = urllib.request.Request(u, headers={'User-Agent': UA})
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        body = resp.read()
                    txt = body.decode('utf-8', 'replace')
                    info = f'200 {len(body)} bytes | {txt[:160]!r}'
                    if r.get('save'):
                        import gzip
                        n = len(lines_out)
                        folder = os.path.join(DATA, 'tests', 'probe', name[:-5])
                        os.makedirs(folder, exist_ok=True)
                        with gzip.open(os.path.join(folder, f'{n:02d}.json.gz'), 'wb') as f:
                            f.write(body)
                        info += f' | saved probe/{name[:-5]}/{n:02d}.json.gz'
                    if u.endswith('.csv'):
                        rows = txt.splitlines()
                        hdr = rows[0].split(',')
                        info += f' | cols {hdr[:40]} | last {rows[-1][:300]!r}'
                        for col in ('week', 'season'):
                            if col in hdr:
                                i = hdr.index(col)
                                vals = sorted({x.split(',')[i] for x in rows[1:] if len(x.split(',')) > i})
                                info += f' | {col}s {vals[-6:]}'
                except urllib.error.HTTPError as e:
                    info = f'HTTP {e.code}'
                except Exception as e:
                    info = f'error {e!r}'
                lines_out.append(f'{u}\n  {info}\n')
            os.makedirs(os.path.join(DATA, 'tests'), exist_ok=True)
            with open(os.path.join(DATA, 'tests', 'probe.txt'), 'w') as f:
                f.write(iso(now()) + '\n' + '\n'.join(lines_out))
            r['fulfilled_at'] = iso(now())
            save(path, r)
            continue
        if r.get('mode') == 'extra':
            pull_extra(date, r.get('markets') or EXTRA_MARKETS)
            r['fulfilled_at'] = iso(now())
            save(path, r)
            continue
        if r.get('mode') == 'latecheck':
            latecheck(date)
            r['fulfilled_at'] = iso(now())
            save(path, r)
            continue
        if r.get('mode') == 'gradetest':                       # diagnostics, no credits
            import io, contextlib
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    gradetest(date)
            except BaseException as e:
                buf.write(f'\nstopped: {e!r}\n')
            os.makedirs(os.path.join(DATA, 'tests'), exist_ok=True)
            with open(os.path.join(DATA, 'tests', f'gradetest-{date}.txt'), 'w') as f:
                f.write(buf.getvalue())
            r['fulfilled_at'] = iso(now())
            save(path, r)
            continue
        prev = load(os.path.join(day_dir(date), 'candidates.json'), {})
        if prev.get('pulled_at') and now() - parse(prev['pulled_at']) < timedelta(minutes=20):
            log(f'request {date}: pulled {prev["pulled_at"]}, reusing (saves credits)')
        else:
            evs = evs or fetch_events()
            pull(date, evs)
        r['fulfilled_at'] = iso(now())
        save(path, r)


# ---------------------------------------------------------------- closing lines
def capture_closing(evs):
    t = now()
    # Closing lines only grade the card's own game-line plays (closing-line value),
    # so a window with none of them costs nothing.
    wanted, prop_mk = set(), {}
    for pk in load(LEDGER, []):
        if pk.get('result') not in (None, 'pending'):
            continue
        for l in (pk.get('legs') or [pk]):
            if l.get('market') in LINE_MARKETS:
                wanted.add(l.get('event_id'))
            elif l.get('market'):
                prop_mk.setdefault(l.get('event_id'), set()).add(l['market'])
    changed = capture_closing_props(evs, prop_mk, t)
    soon = [e for e in evs if timedelta(0) < parse(e['commence_time']) - t <= timedelta(minutes=CLOSE_WINDOW_MIN)
            and e['id'] in wanted]
    need = []
    for e in soon:
        cl = load(os.path.join(day_dir(et_date(e['commence_time'])), 'closing.json'), {})
        if e['id'] not in cl:
            need.append(e)
    if not need:
        return changed
    if remaining() < CLOSE_MIN_CREDITS:
        log('closing: skipped, credits low')
        return changed
    ids = {e['id'] for e in need}
    lines = odds('/odds', bookmakers=','.join(BOOKS), markets=','.join(LINE_MARKETS), oddsFormat='american')
    for g in lines:
        if g['id'] not in ids:
            continue
        path = os.path.join(day_dir(et_date(g['commence_time'])), 'closing.json')
        cl = load(path, {})
        cl[g['id']] = {'captured_at': iso(t), 'commence': g['commence_time'],
                       'bookmakers': [b for b in g['bookmakers'] if b['key'] in ('draftkings', 'pinnacle', 'fanduel',
                                                                                  'betmgm', 'williamhill_us', 'betrivers',
                                                                                  'fanatics', 'betonlineag', 'bovada', 'lowvig')]}
        save(path, cl, compact=True)
        changed = True
        log(f'closing: captured {abbr(g["away_team"])} @ {abbr(g["home_team"])}')
    return changed


def capture_closing_props(evs, prop_mk, t):
    """Closing prices for the card's props: one credit per market on the card, per game."""
    changed = False
    for e in evs:
        mks = sorted(prop_mk.get(e['id'], ()))
        if not mks or not (timedelta(0) < parse(e['commence_time']) - t <= timedelta(minutes=CLOSE_WINDOW_MIN)):
            continue
        path = os.path.join(day_dir(et_date(e['commence_time'])), 'closing-props.json')
        cl = load(path, {})
        if e['id'] in cl:
            continue
        if remaining() - len(mks) < CLOSE_MIN_CREDITS:
            log('closing props: skipped, credits low')
            continue
        try:
            g = odds(f'/events/{e["id"]}/odds', bookmakers=','.join(BOOKS),
                     markets=','.join(mks), oddsFormat='american')
        except SystemExit as err:
            log(f'closing props failed for {e["id"]}: {err}')
            continue
        cl[e['id']] = {'captured_at': iso(t), 'commence': e['commence_time'],
                       'bookmakers': g.get('bookmakers', [])}
        save(path, cl, compact=True)
        changed = True
        log(f'closing props: captured {abbr(e["away_team"])} @ {abbr(e["home_team"])} ({len(mks)} markets)')
    return changed


def capture_opener(evs):
    """Morning game-line snapshot for the day (3 credits), so the pre-kickoff pull can
    show how Pinnacle and DraftKings moved. Once per ET date."""
    t = now()
    by_date = {}
    for e in evs:
        if parse(e['commence_time']) > t:
            by_date.setdefault(et_date(e['commence_time']), []).append(e)
    for date, games in by_date.items():
        lead = min(parse(e['commence_time']) for e in games) - t
        if not (timedelta(hours=OPENER_MIN_LEAD_H) <= lead <= timedelta(hours=OPENER_HOURS)):
            continue
        path = os.path.join(day_dir(date), 'opener.json')
        if os.path.exists(path) or os.path.exists(os.path.join(day_dir(date), 'candidates.json')):
            continue
        if remaining() < OPENER_MIN_CREDITS:
            log('opener: skipped, credits low')
            continue
        ids = {e['id'] for e in games}
        lines = odds('/odds', bookmakers=','.join(BOOKS), markets=','.join(LINE_MARKETS), oddsFormat='american')
        save(path, {'captured_at': iso(t), 'games': [g for g in lines if g['id'] in ids]}, compact=True)
        log(f'opener: {date} game lines captured ({len(ids)} games)')
        return True
    return False


# ---------------------------------------------------------------- grading
def norm_name(s):
    s = (s or '').lower()
    for suf in (' jr.', ' jr', ' sr.', ' sr', ' iii', ' ii', ' iv', ' v'):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return ''.join(ch for ch in s if ch.isalpha())


def espn_scoreboard(date):
    j, _ = http_json(f'{ESPN}/scoreboard?dates={date.replace("-", "")}')
    return j.get('events', [])


def espn_box(event_id):
    j, _ = http_json(f'{ESPN}/summary?event={event_id}')
    out = {}
    for side in (j.get('boxscore') or {}).get('players', []) or []:
        for cat in side.get('statistics', []) or []:
            keys = cat.get('keys', []) or []
            for a in cat.get('athletes', []) or []:
                ath = a.get('athlete') or {}
                nm = ath.get('displayName') or ath.get('shortName')
                if not nm:
                    continue
                rec = out.setdefault(norm_name(nm), {'name': nm})
                for i, v in enumerate(a.get('stats', []) or []):
                    if i >= len(keys):
                        continue
                    ks = keys[i].replace('-', '/').split('/')
                    vs = str(v).replace('-', '/').split('/')
                    if len(ks) > 1 and len(ks) == len(vs):
                        for kk, vv in zip(ks, vs):
                            rec[kk] = _num(vv)
                    else:
                        rec[keys[i]] = _num(v)
    return out


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def find_espn_game(events, home, away):
    for ev in events:
        comp = (ev.get('competitions') or [{}])[0]
        teams = {c.get('homeAway'): c for c in comp.get('competitors', [])}
        h = teams.get('home', {}).get('team', {}).get('displayName')
        a = teams.get('away', {}).get('team', {}).get('displayName')
        if h == home and a == away:
            return ev, comp, teams
    return None, None, None


def grade_leg(leg, game_cache, box_cache):
    """'win' | 'loss' | 'push' | 'check' (needs a human look) | None (not final yet)."""
    date = et_date(leg['commence'])
    if date not in game_cache:
        game_cache[date] = espn_scoreboard(date)
    ev, comp, teams = find_espn_game(game_cache[date], leg['home'], leg['away'])
    if not ev:
        return 'check', 'game not found on ESPN'
    if not comp.get('status', {}).get('type', {}).get('completed'):
        return None, 'not final'
    hs = _num(teams['home'].get('score'))
    as_ = _num(teams['away'].get('score'))
    mk, subj, side, pt = leg['market'], leg['subject'], leg['side'], leg['point']
    if mk in ('h2h', 'spreads'):
        mine, theirs = (hs, as_) if subj == leg['home'] else (as_, hs)
        m = mine - theirs + ((pt or 0) if mk == 'spreads' else 0)
        return ('win' if m > 0 else 'loss' if m < 0 else 'push'), f'{abbr(leg["away"])} {as_:g} - {abbr(leg["home"])} {hs:g}'
    if mk == 'totals':
        tot = hs + as_
        m = tot - pt if side == 'Over' else pt - tot
        return ('win' if m > 0 else 'loss' if m < 0 else 'push'), f'total {tot:g}'
    if ev['id'] not in box_cache:
        box_cache[ev['id']] = espn_box(ev['id'])
    rec = box_cache[ev['id']].get(norm_name(subj))
    if mk == 'player_anytime_td':
        if not rec:
            return 'check', f'{subj} not in box score (did not play = void, played = loss)'
        tds = rec.get('rushingTouchdowns', 0) + rec.get('receivingTouchdowns', 0)
        return ('win' if tds >= 1 else 'loss'), f'{tds:g} TD'
    stat = ESPN_STAT.get(mk)
    if not stat:
        return 'check', 'market not graded automatically'
    if not rec:
        return 'check', f'{subj} not in box score (did not play = void, played = 0)'
    v = rec.get(stat, 0)
    m = v - pt if side == 'Over' else pt - v
    return ('win' if m > 0 else 'loss' if m < 0 else 'push'), f'{v:g} {PROP_SHORT[mk]}'


def closing_clv(leg):
    """Closing-line value for a leg: our price against Pinnacle's no-vig close."""
    fname = 'closing.json' if leg['market'] in LINE_MARKETS else 'closing-props.json'
    cl = load(os.path.join(day_dir(et_date(leg['commence'])), fname), {}).get(leg['event_id'])
    if not cl:
        return None
    event = {'home_team': leg['home'], 'away_team': leg['away']}
    books = {b['key']: index_book(b, event) for b in cl['bookmakers']}
    key = (leg['market'], leg['subject'], leg['side'], leg['point'])
    f = fair_for(key, event, books)
    if f['source'] is None:
        return None
    out = {'close_fair_prob': round(f['p'], 4), 'close_fair_price': fair_american(f['p']),
           'close_source': f['source'], 'clv': round(f['p'] * dec(leg['dk_price']) - 1, 4)}
    dk = books.get('draftkings', {}).get(key)
    if dk is not None:
        out['close_dk_price'] = dk
    return out


def grade():
    ledger = load(LEDGER, [])
    if not ledger:
        return False
    t = now()
    game_cache, box_cache = {}, {}
    changed = False
    for pick in ledger:
        if pick.get('result') not in (None, 'pending'):
            continue
        legs = pick['legs'] if pick.get('kind') == 'parlay' else [pick]
        if any(parse(l['commence']) + timedelta(hours=3, minutes=15) > t for l in legs):
            continue
        results = []
        for l in legs:
            if l.get('result') in ('win', 'loss', 'push', 'void'):
                results.append(l['result'])
                continue
            try:
                r, why = grade_leg(l, game_cache, box_cache)
            except Exception as e:                                  # ESPN hiccup: try next tick
                log(f'grade: {pick["id"]} {e}')
                r, why = None, str(e)
            if r is None:
                results.append(None)
                continue
            l['result'], l['result_note'] = r, why
            if l is not pick:
                changed = True
            results.append(r)
            if 'clv' not in l:
                c = closing_clv(l)
                if c:
                    l.update(c)
        if None in results:
            continue
        units = pick.get('units', 1)
        if 'check' in results:
            pick['result'] = 'check'
        elif 'loss' in results:
            pick['result'], pick['pl'] = 'loss', -units
        else:
            d = 1.0
            for l, r in zip(legs, results):
                if r == 'win':
                    d *= dec(l['dk_price'])
            pick['result'] = 'win' if d > 1 else 'push'
            if pick.get('kind') == 'parlay' and d > 1:
                pick['pl'] = round(units * (d - 1), 3)
            elif d > 1:
                pick['pl'] = round(units * (dec(pick['dk_price']) - 1), 3)
            else:
                pick['pl'] = 0.0
        pick['graded_at'] = iso(t)
        changed = True
        log(f'graded {pick["id"]} {pick.get("label")}: {pick["result"]} {pick.get("pl")}')
    if changed:
        save(LEDGER, ledger)
        write_summary(ledger)
    return changed


def write_summary(ledger):
    done = [p for p in ledger if p.get('result') in ('win', 'loss', 'push')]
    def block(ps):
        risked = sum(p.get('units', 1) for p in ps if p['result'] != 'push')
        pl = sum(p.get('pl') or 0 for p in ps)
        clvs = [p['clv'] for p in ps if p.get('clv') is not None]
        return {'picks': len(ps),
                'won': sum(p['result'] == 'win' for p in ps),
                'lost': sum(p['result'] == 'loss' for p in ps),
                'push': sum(p['result'] == 'push' for p in ps),
                'units': round(pl, 2),
                'roi': round(pl / risked, 4) if risked else None,
                'avg_ev_at_bet': round(sum(p.get('ev', 0) for p in ps) / len(ps), 4) if ps else None,
                'avg_clv': round(sum(clvs) / len(clvs), 4) if clvs else None,
                'clv_n': len(clvs)}
    groups = {}
    for p in done:
        g = 'parlays' if p.get('kind') == 'parlay' else ('game lines' if p['market'] in LINE_MARKETS else
                                                          'anytime TD' if p['market'] == 'player_anytime_td' else 'yardage props')
        groups.setdefault(g, []).append(p)
    save(SUMMARY, {'updated': iso(now()), 'season': block(done),
                   'by_type': {k: block(v) for k, v in sorted(groups.items())},
                   'pending': sum(1 for p in ledger if p.get('result') in (None, 'pending')),
                   'scratched': sum(1 for p in ledger if p.get('result') == 'scratched'),
                   'to_check': [p['id'] for p in ledger if p.get('result') == 'check']})


# ---------------------------------------------------------------- parlay helper
def price_parlay(date, ids):
    cands = {c['id']: c for c in load(os.path.join(day_dir(date), 'candidates.json'), {}).get('candidates', [])}
    legs = [cands[i] for i in ids]
    games = [l['event_id'] for l in legs]
    d = 1.0
    p = 1.0
    for l in legs:
        d *= dec(l['dk_price'])
        p *= l['fair_prob']
    res = {'legs': [l['label'] for l in legs], 'dk_price_if_priced_as_product': to_american(d),
           'fair_prob': round(p, 4), 'fair_price': fair_american(p), 'ev': round(p * d - 1, 4)}
    if len(set(games)) < len(games):
        res['warning'] = ('two legs share a game: DraftKings prices that as a same-game parlay, '
                          'not as the product, and the legs are not independent. Do not use this number.')
    print(json.dumps(res, indent=1))


# ---------------------------------------------------------------- late check
def latecheck(date):
    """Fresh game lines (3 credits, whole slate) for the card's game-line plays in
    games that have not kicked off. Props are not re-priced: that would cost 4
    credits a game. Writes bets/data/<date>/late.json for the session to judge."""
    d = day_dir(date)
    card = load(os.path.join(d, 'card.json'))
    out = {'date': date, 'checked_at': iso(now()), 'credits_left': remaining(), 'picks': []}
    if not card:
        out['note'] = 'no card for this date'
        save(os.path.join(d, 'late.json'), out)
        return
    t = now()
    legs = []
    for p in card.get('picks', []):
        for l in (p.get('legs') or [p]):
            if l.get('market') in LINE_MARKETS and parse(l['commence']) > t and not p.get('scratched'):
                legs.append((p, l))
    if not legs:
        out['note'] = 'no game-line plays still to come: nothing to re-price, no credits spent'
        save(os.path.join(d, 'late.json'), out)
        log('latecheck: nothing to re-price')
        return
    prev = load(os.path.join(d, 'late.json'), {})
    if prev.get('checked_at') and prev.get('picks') and t - parse(prev['checked_at']) < timedelta(minutes=20):
        log('latecheck: checked in the last 20 minutes, reusing')
        return
    if remaining() < LATE_MIN_CREDITS:
        out['note'] = f'skipped to protect the monthly budget ({remaining()} credits left, late checks need {LATE_MIN_CREDITS}+)'
        save(os.path.join(d, 'late.json'), out)
        log('latecheck: ' + out['note'])
        return
    games = {g['id']: g for g in odds('/odds', bookmakers=','.join(BOOKS), markets=','.join(LINE_MARKETS),
                                       oddsFormat='american')}
    for p, l in legs:
        g = games.get(l['event_id'])
        row = {'id': p['id'], 'label': l['label'], 'card_price': l['dk_price'], 'card_ev': l['ev']}
        if not g:
            row['note'] = 'game not in the feed'
            out['picks'].append(row)
            continue
        books = {b['key']: index_book(b, g) for b in g['bookmakers']}
        key = (l['market'], l['subject'], l['side'], l['point'])
        f = fair_for(key, g, books)
        now_dk = books.get('draftkings', {}).get(key)
        if f['source']:
            row['fair_prob_now'] = round(f['p'], 4)
            row['fair_price_now'] = fair_american(f['p'])
            row['source_now'] = f['source']
            # what the bet is worth now at the card's price, if it has already been placed
            row['ev_at_card_price_now'] = round(f['p'] * dec(l['dk_price']) - 1, 4)
            if now_dk is not None:
                row['ev_now'] = round(f['p'] * dec(now_dk) - 1, 4)
        row['dk_price_now'] = now_dk
        if now_dk is None:
            alts = sorted(k[3] for k in books.get('draftkings', {}) if k[:3] == key[:3] and k[3] is not None)
            row['note'] = f'DraftKings no longer offers {l["point"]:g}; now offering {alts}'
        out['picks'].append(row)
    out['credits_left'] = remaining()
    save(os.path.join(d, 'late.json'), out)
    log(f'latecheck {date}: {len(out["picks"])} game-line plays re-priced')


def apply_late(date, spec_path):
    """Record the session's late-check decisions: scratch plays that no longer hold."""
    spec = load(spec_path) or {}
    d = day_dir(date)
    card = load(os.path.join(d, 'card.json'))
    if not card:
        raise SystemExit(f'no card for {date}')
    t = iso(now())
    why = {x['id']: x.get('why', '') for x in spec.get('scratch', [])}
    known = {p['id'] for p in card['picks']}
    bad = [i for i in why if i not in known]
    for p in card['picks']:
        if p['id'] in why and not p.get('scratched'):
            if any(parse(l['commence']) <= now() for l in (p.get('legs') or [p])):
                bad.append(p['id'] + ' (already started)')
                continue
            p['scratched'], p['scratch_reason'], p['scratched_at'] = True, why[p['id']], t
    card.setdefault('late_checks', []).append({'at': t, 'games': spec.get('games', []),
                                              'scratched': [i for i in why if i in known],
                                              'note': spec.get('note', '')})
    card['total_units'] = round(sum(p['units'] for p in card['picks'] if not p.get('scratched')), 2)
    save(os.path.join(d, 'card.json'), card)
    ledger = load(LEDGER, [])
    for e in ledger:
        if e.get('date') == date and e['id'] in why and e.get('result') in (None, 'pending'):
            e['result'], e['pl'], e['scratch_reason'] = 'scratched', 0.0, why[e['id']]
    save(LEDGER, ledger)
    write_summary(ledger)
    save(os.path.join(HERE, 'latest.json'), {'date': date, 'generated_at': card['generated_at'], 'updated_at': t})
    print(f'late check recorded for {date}: {len(card["late_checks"][-1]["scratched"])} scratched')
    if bad:
        print('PROBLEM: not scratched: ' + ', '.join(bad))
        raise SystemExit(1)


# ---------------------------------------------------------------- record the card
def record(date, picks_path):
    """Turn the pre-kickoff session's choices (candidate ids + units + reasons) into
    the day's card, the ledger entries and the pointer the picks page reads."""
    spec = load(picks_path)
    cj = load(os.path.join(day_dir(date), 'candidates.json'), {})
    cands = {c['id']: c for c in cj.get('candidates', [])}
    t = iso(now())
    entries, problems = [], []
    for p in spec.get('picks', []):
        c = cands.get(p['id'])
        if not c:
            problems.append(f'unknown candidate id {p["id"]}')
            continue
        e = dict(c, kind='single', units=p.get('units', 1), reason=p.get('reason', ''),
                 date=date, recorded_at=t, result=None, pl=None)
        entries.append(e)
    for par in spec.get('parlays', []):
        legs = [cands.get(i) for i in par['ids']]
        if None in legs:
            problems.append(f'parlay has unknown ids {par["ids"]}')
            continue
        if len({l['event_id'] for l in legs}) < len(legs):
            problems.append(f'parlay {par["ids"]} has two legs from one game (same-game parlay) - not recorded')
            continue
        d, pr = 1.0, 1.0
        for l in legs:
            d *= dec(l['dk_price'])
            pr *= l['fair_prob']
        entries.append({'id': 'p' + hashlib.sha1('|'.join(sorted(par['ids'])).encode()).hexdigest()[:7],
                        'kind': 'parlay', 'label': 'Parlay: ' + ' + '.join(l['label'] for l in legs),
                        'legs': [dict(l, result=None) for l in legs], 'dk_price': to_american(d),
                        'fair_prob': round(pr, 4), 'fair_price': fair_american(pr), 'ev': round(pr * d - 1, 4),
                        'commence': min(l['commence'] for l in legs), 'units': par.get('units', 0.5),
                        'reason': par.get('reason', ''), 'date': date, 'recorded_at': t,
                        'result': None, 'pl': None})
    if problems:
        print('\n'.join('PROBLEM: ' + x for x in problems))
    ledger = load(LEDGER, [])
    keep = [x for x in ledger if not (x.get('date') == date and x.get('result') in (None, 'pending'))]
    graded_today = {x['id'] for x in keep if x.get('date') == date}
    ledger = keep + [e for e in entries if e['id'] not in graded_today]
    save(LEDGER, ledger)
    games = []
    for g in cj.get('games', []):
        gp = [e['id'] for e in entries if e.get('event_id') == g['event_id']]
        games.append(dict(g, picks=gp, note=(spec.get('games') or {}).get(g['event_id'])
                          or (spec.get('games') or {}).get(g['game'], '')))
    card = {'date': date, 'generated_at': t, 'headline': spec.get('headline', ''),
            'total_units': round(sum(e['units'] for e in entries), 2),
            'games': games, 'picks': entries, 'passed': spec.get('passed', []),
            'notes': spec.get('notes', []), 'credits_left': remaining()}
    save(os.path.join(day_dir(date), 'card.json'), card)
    save(os.path.join(HERE, 'latest.json'), {'date': date, 'generated_at': t})
    write_summary(ledger)
    print(f'recorded {len(entries)} plays ({card["total_units"]}u) for {date}')
    if problems:
        raise SystemExit(1)


# ---------------------------------------------------------------- self test
def selftest():
    t = os.path.join(REPO, 'data', 'odds-test')
    lines = json.load(open(os.path.join(t, 'lines.json')))
    props = json.load(open(os.path.join(t, 'props.json')))
    ev = {'id': props['id'], 'home_team': props['home_team'], 'away_team': props['away_team'],
          'commence_time': props['commence_time']}
    game = next(g for g in lines if g['id'] == ev['id'])
    merged = {}
    for b in game['bookmakers'] + props['bookmakers']:
        m = merged.setdefault(b['key'], {'key': b['key'], 'last_update': b.get('last_update'), 'markets': []})
        m['markets'] += b['markets']
    cands = evaluate(ev, list(merged.values()))
    by = {}
    for c in cands:
        by.setdefault(c['source'], 0)
        by[c['source']] += 1
    print(f'{len(cands)} DraftKings selections priced: {by}')
    for c in sorted(cands, key=lambda c: -c['ev'])[:12]:
        print(f'{c["ev"]*100:+6.1f}%  {c["label"]:38} DK {c["dk_price"]:+5d}  fair {c["fair_price"]:+5d}  {c["source"]}'
              + (f' from {c["from_point"]:g}' if c.get('from_point') is not None else ''))
    # math checks
    assert abs(dec(-110) - 1.909090909) < 1e-6 and abs(dec(150) - 2.5) < 1e-9
    assert fair_american(0.5) == -100 and fair_american(0.25) == 300
    assert abs(shift('spreads', 'cover', -2.5, 0.52, -2.5) - 0.52) < 1e-9
    assert shift('spreads', 'cover', -2.5, 0.52, -3.5) is None        # crosses 3
    p = shift('player_reception_yds', 'Over', 50.5, 0.5, 52.5)
    assert p < 0.5 and p > 0.4, p
    print('math checks passed')


def gradetest(date):
    """Check ESPN parsing against finished games: builds picks whose answers are known."""
    evs = espn_scoreboard(date)
    done = [e for e in evs if e['competitions'][0]['status']['type'].get('completed')][:2]
    gc, bc = {date: evs}, {}
    ok = True
    for ev in done:
        comp = ev['competitions'][0]
        teams = {c['homeAway']: c for c in comp['competitors']}
        home, away = teams['home']['team']['displayName'], teams['away']['team']['displayName']
        hs, as_ = _num(teams['home']['score']), _num(teams['away']['score'])
        base = {'home': home, 'away': away, 'commence': iso(parse(ev['date'].replace('Z', ':00Z')) if len(ev['date']) == 17 else parse(ev['date'])),
                'event_id': 'test'}
        box = espn_box(ev['id'])
        bc[ev['id']] = box
        legs = [dict(base, market='h2h', subject=home, side='win', point=None, want='win' if hs > as_ else 'loss' if hs < as_ else 'push'),
                dict(base, market='totals', subject='game', side='Over', point=hs + as_ - 0.5, want='win')]
        top = {}
        for rec in box.values():
            for st in ('passingYards', 'rushingYards', 'receivingYards'):
                if st in rec and rec[st] > top.get(st, (None, -1))[1]:
                    top[st] = (rec['name'], rec[st])
        mk_for = {'passingYards': 'player_pass_yds', 'rushingYards': 'player_rush_yds', 'receivingYards': 'player_reception_yds'}
        for st, (nm, v) in top.items():
            legs.append(dict(base, market=mk_for[st], subject=nm, side='Under', point=v + 0.5, want='win'))
        scorer = next((r['name'] for r in box.values() if r.get('rushingTouchdowns', 0) + r.get('receivingTouchdowns', 0) >= 1), None)
        if scorer:
            legs.append(dict(base, market='player_anytime_td', subject=scorer, side='Yes', point=None, want='win'))
        for l in legs:
            r, why = grade_leg(l, gc, bc)
            good = r == l['want']
            ok &= good
            print(f'{"ok " if good else "BAD"} {abbr(away)}@{abbr(home)} {l["market"]:22} {l["subject"]:24} {l["side"]:5} {l["point"]}  -> {r} ({why})')
    print('gradetest', 'passed' if ok and done else 'FAILED')
    if not (ok and done):
        raise SystemExit(1)


# ---------------------------------------------------------------- main
def main(argv):
    mode = argv[1] if len(argv) > 1 else 'tick'
    if mode == 'selftest':
        return selftest()
    if mode == 'parlay':
        return price_parlay(argv[2], argv[3:])
    if mode == 'grade':
        return grade()
    if mode == 'gradetest':
        return gradetest(argv[2])
    if mode == 'record':
        return record(argv[2], argv[3])
    if mode == 'late':
        return apply_late(argv[2], argv[3])
    if mode == 'pull':
        return pull(argv[2])
    if mode == 'request':
        return run_requests()
    if mode == 'tick':
        evs = fetch_events()
        write_schedule(evs)
        capture_opener(evs)
        capture_closing(evs)
        grade()
        return
    raise SystemExit(f'unknown mode {mode}')


if __name__ == '__main__':
    main(sys.argv)

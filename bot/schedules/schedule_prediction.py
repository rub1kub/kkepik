"""Read-only estimates based on the latest published revision of each past date."""
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
import json
from math import log
from pathlib import Path
import re
import sqlite3
from typing import NamedTuple
import unicodedata

from college_calendar import day_rule

HORIZON_DAYS = 7
MIN_MATCHING_DAYS = 5
VALIDATION_DAYS = 8
RECENT_TRIALS = 24
PRACTICE_DURATION = timedelta(days=14)
PRACTICE_GAP = timedelta(days=7)
TRANSFER_LOOKBACK = timedelta(days=112)
CONTEXT_LAGS = tuple(timedelta(days=lag) for lag in range(1, 7))
QUOTES = str.maketrans('«»“”', '""""')
PRACTICE_SUBJECT = re.compile(r'^(?:(?:учебная|производственная|преддипломная)\s+)*практика\b', re.I)


class Model(NamedTuple):
    half_life: int
    parity_weight: int
    use_context: bool = False


MODELS = (
    Model(21, 1), Model(42, 1), Model(84, 1), Model(42, 2), Model(84, 2),
    Model(42, 6), Model(84, 8), Model(42, 2, True), Model(42, 6, True), Model(84, 8, True),
)


@dataclass(frozen=True)
class SchoolDay:
    day: date
    slots: dict[int, tuple[str, ...]]


class SlotEstimate(NamedTuple):
    subjects: tuple[str, ...]
    support: float
    count: int
    joint_support: float
    presence: float
    days_present: int
    candidates: tuple[tuple[tuple[str, ...], float], ...]

    def can_name_subject(self):
        return bool(self.subjects) and self.support >= 0.58 and self.count >= 3


@dataclass(frozen=True)
class Sample:
    age: int
    same_parity: bool
    context_weight: float
    slots: dict[int, tuple[str, ...]]


@dataclass(frozen=True)
class Trial:
    weekday: tuple[int, bool]
    scores: tuple[float, ...]
    outcomes: tuple[tuple[float, bool], ...]
    presence_outcomes: dict[int, tuple[float, bool]]


def weekend(day: date) -> bool:
    return day.weekday() == 6 or (day_rule(day) or {}).get('kind') == 'holiday'


def week_parity(day: date) -> int:
    # A continuous week number keeps alternating weeks aligned across New Year.
    return (day.toordinal() - 1) // 7 % 2


def subject_key(subjects):
    return tuple(sorted(unicodedata.normalize('NFKC', subject).casefold().replace('ё', 'е')
                        .translate(QUOTES) for subject in subjects))


def weekday_kind(day):
    return day.weekday(), bool(day_rule(day))


def practice_day(row):
    practice_slots = sum(all(PRACTICE_SUBJECT.match(subject) for subject in subjects)
                         for subjects in row.slots.values())
    return practice_slots >= 2 and practice_slots * 2 > len(row.slots)


def teaching_days(history, target):
    practice_start = last_practice = None
    resumed = False
    teaching = []
    for row in sorted(history, key=lambda row: row.day):
        if row.day >= target or weekend(row.day):
            continue
        if practice_day(row):
            if (last_practice is None or row.day - last_practice > PRACTICE_GAP
                    or (resumed and row.day - practice_start >= PRACTICE_DURATION)):
                practice_start = row.day
            last_practice = row.day
            resumed = False
        else:
            resumed |= bool(row.slots)
            teaching.append(row)
    resume_on = practice_start + PRACTICE_DURATION if practice_start else None
    return teaching, resume_on if resume_on and target < resume_on else None


def inactive_subjects(past, target, subjects_by_day):
    current_week = target - timedelta(days=target.weekday())
    published_days = Counter()
    appearances = {}
    for row in past:
        if not row.slots or (target - row.day).days > 84:
            continue
        week = (current_week - row.day).days
        week = (week + 6) // 7
        published_days[week] += 1
        for subject in subjects_by_day[row.day]:
            appearances.setdefault(subject, set()).add(week)
    # Compare completed teaching weeks. A week with little published data is
    # no evidence a subject disappeared, and a recent reappearance clears it.
    complete_weeks = {week for week, days in published_days.items() if week > 0 and days >= 3}
    inactive = set()
    for subject, weeks in appearances.items():
        last = min(weeks)
        missed = sum(week < last for week in complete_weeks)
        if missed < 2:
            continue
        earlier = sorted(week for week in complete_weeks if week >= last)[:6]
        seen = len(weeks.intersection(earlier))
        # Alternating subjects need three full missed weeks, weekly ones two.
        if seen >= 3 and (seen * 3 >= len(earlier) * 2 or missed >= 3):
            inactive.add(subject)
    return inactive


def next_study_day(day):
    day += timedelta(days=1)
    while weekend(day):
        day += timedelta(days=1)
    return day


def missed_subject(prior, actual, already_present):
    if not actual or len(prior) < MIN_MATCHING_DAYS:
        return None
    if sum(len(week) > len(actual) for week in prior) * 4 < len(prior) * 3:
        return None
    # A subject can regularly fall on the same weekday at different times.
    counts = Counter(subject for week in prior for subject in {key for n, key in week.items() if n <= 4}
                     if not already_present.intersection(subject))
    return next((subject for subject, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
                 if count >= 3 and count / len(prior) >= 0.60), None)


def transfer_cases(past, target, keys, subjects_by_day, inactive):
    previous_weeks = {}
    cases_by_delay = {1: [], 2: []}
    current = None
    for row in past:
        if target - row.day > TRANSFER_LOOKBACK:
            continue
        slots = keys[row.day]
        similar = previous_weeks.setdefault(weekday_kind(row.day), [])
        missed = missed_subject(similar[-8:], slots, subjects_by_day[row.day])
        if missed:
            following = row.day
            for delay in cases_by_delay:
                following = next_study_day(following)
                if following == target and inactive.isdisjoint(missed):
                    current = (delay, missed)
                    break
                following_slots = keys.get(following)
                if not following_slots:
                    break
                cases_by_delay[delay].append((following, missed, following_slots))
                if not subjects_by_day[following].isdisjoint(missed):
                    break
        similar.append(slots)
    if current is None:
        return None, []
    delay, subject = current
    cases = cases_by_delay[delay][-16:]
    if len(cases) < 3:
        return None, []
    return subject, [(1 + (day.weekday() == target.weekday()) + (missing == subject), missing, slots)
                     for day, missing, slots in cases]


def apply_transfers(votes, current, cases):
    if not cases:
        return set()
    evidence = sum(weight for weight, _, _ in cases)
    changed = set()
    for n in range(1, 5):
        slot = votes.get(n, Counter())
        baseline = slot.total()
        presence = baseline
        if n == 4:
            seen = sum(weight for weight, _, slots in cases if n in slots)
            presence = max(baseline, (baseline * 3 + seen) / (3 + evidence))
        joint = slot.get(current, 0)
        transfers = [weight for weight, missing, slots in cases if slots.get(n) == missing]
        if len(transfers) >= 2:
            joint = min(presence, max(joint, (joint * 3 + sum(transfers)) / (3 + evidence)))
        if presence <= baseline and joint <= slot.get(current, 0):
            continue
        if n == 4 and presence > baseline:
            changed.add(n)
        alternatives = baseline - slot.get(current, 0)
        adjusted = Counter({key: chance * (presence - joint) / alternatives for key, chance in slot.items()
                            if key != current and chance > 0}) if alternatives > 0 else Counter({(): presence - joint})
        if joint:
            adjusted[current] = joint
        votes[n] = adjusted
    return changed


def sample_days(history, target, keys, subjects_by_day):
    target_context = [(lag, subjects_by_day[target - lag]) for lag in CONTEXT_LAGS if target - lag in subjects_by_day]
    target_parity = week_parity(target)
    samples = []
    for row in reversed(history):
        similarities = []
        for lag, recent in target_context:
            earlier = subjects_by_day.get(row.day - lag)
            if earlier is not None:
                union = earlier | recent
                similarities.append(len(earlier & recent) / len(union) if union else 1)
        # Shrink short or missing windows toward a neutral match.
        context_weight = 0.5 + 1.5 * (sum(similarities) + 1) / (len(similarities) + 2)
        samples.append(Sample((target - row.day).days, week_parity(row.day) == target_parity,
                              context_weight, keys[row.day]))
    return samples


def distribution(samples, model):
    total = 0.0
    votes = {}
    for sample in samples:
        weight = 2 ** (-sample.age / model.half_life)
        if sample.same_parity:
            weight *= model.parity_weight
        if model.use_context:
            weight *= sample.context_weight
        total += weight
        for n, key in sample.slots.items():
            votes.setdefault(n, Counter())[key] += weight
    return {n: Counter({key: weight / total for key, weight in slot.items()}) for n, slot in votes.items()}


def log_score(votes, actual):
    scores = []
    for n in votes.keys() | actual.keys():
        slot = votes.get(n)
        if n in actual:
            chance = slot.get(actual[n], 0) if slot else 0
        else:
            chance = 1 - slot.total()
        # Give unseen subjects small mass during validation so one substitute
        # cannot determine which model is used for the rest of the week.
        scores.append(log(0.05 + 0.95 * chance))
    return sum(scores) / len(scores) if scores else 0


def model_weights(trials, weekday):
    recent = trials[-RECENT_TRIALS:]
    same_day = [trial for trial in recent if trial.weekday == weekday]
    scores = []
    for i in range(len(MODELS)):
        shared = sum(trial.scores[i] for trial in recent) / max(1, len(recent))
        local = sum(trial.scores[i] for trial in same_day)
        scores.append((local + shared * 6) / (len(same_day) + 6))
    best = max(scores)
    weights = [2 ** ((score - best) * 7) * (2 if model.parity_weight >= 6 else 1)
               for score, model in zip(scores, MODELS)]
    total = sum(weights)
    return [weight / total for weight in weights]


def combine(votes_by_model, weights, samples, rows, *, inactive=frozenset(), transfer=None, cases=(), transfer_label=()):
    votes = {}
    counts = {}
    labels = {}
    for weight, model_votes in zip(weights, votes_by_model):
        for n, slot in model_votes.items():
            combined = votes.setdefault(n, Counter())
            for key, chance in slot.items():
                combined[key] += weight * chance
    for sample, row in zip(samples, reversed(rows)):
        for n, key in sample.slots.items():
            counts.setdefault(n, Counter())[key] += 1
            labels.setdefault((n, key), row.slots[n])
    for slot in votes.values():
        for key, chance in list(slot.items()):
            if not inactive.isdisjoint(key):
                slot[key] = chance * 0.25
                slot[()] += chance * 0.75
    adjusted_slots = apply_transfers(votes, transfer, cases)
    winners = {}
    for n, slot in sorted(votes.items()):
        presence = slot.total()
        if presence < (0.50 if n == 4 else 0.65):
            continue
        ranked = [(key, chance) for key, chance in slot.most_common() if chance > 0]
        key, chance = ranked[0]
        slot_labels = {candidate: labels.get((n, candidate), transfer_label if candidate == transfer else ())
                       for candidate, _ in ranked}
        candidates = tuple((slot_labels[candidate], weight / presence) for candidate, weight in ranked)
        observations = counts.get(n, Counter())
        transfer_support = sum(slots.get(n) == missed for _, missed, slots in cases) if key == transfer else 0
        winners[n] = SlotEstimate(slot_labels[key], chance / presence, max(observations[key], transfer_support),
                                  chance, presence, observations.total(), candidates)
    return winners, adjusted_slots


def validate(history, keys, subjects_by_day):
    prior_by_weekday = {}
    trials = []
    for index, row in enumerate(history):
        weekday = weekday_kind(row.day)
        earlier = prior_by_weekday.setdefault(weekday, [])
        if len(earlier) >= MIN_MATCHING_DAYS and index >= len(history) - RECENT_TRIALS * 2:
            samples = sample_days(earlier, row.day, keys, subjects_by_day)
            models = [distribution(samples, model) for model in MODELS]
            actual = keys[row.day]
            predicted, _ = combine(models, model_weights(trials, weekday), samples, earlier)
            outcomes = tuple((slot.joint_support, subject_key(slot.subjects) == actual.get(n, ()))
                             for n, slot in predicted.items() if slot.can_name_subject())
            presence_outcomes = {n: (slot.presence, n in actual) for n, slot in predicted.items()}
            trials.append(Trial(weekday, tuple(log_score(votes, actual) for votes in models), outcomes, presence_outcomes))
        earlier.append(row)
    return trials


def probability(support, sample_count, outcomes, *, step=5):
    # Shrink the weighted frequency toward the past reliability of forecasts
    # for the same event with similar support.
    scores = []
    for day in outcomes:
        matches = [hit for previous, hit in day if abs(support - previous) <= 0.15]
        if matches:
            scores.append(sum(matches) / len(matches))
    evidence = min(sample_count, VALIDATION_DAYS)
    chance = (1 + evidence * support + sum(scores)) / (2 + evidence + len(scores))
    return min(95, max(5, step * round(chance * 100 / step)))


def subject_options(slot):
    # Conditional on this period taking place. One extra observation gives
    # unseen replacements a small share which decreases as evidence grows.
    unseen = 1 / (max(slot.days_present, slot.count) + 1)
    shares = [(' / '.join(names), 100 * share * (1 - unseen)) for names, share in slot.candidates if names]
    unknown = sum(chance for names, chance in slot.candidates if not names)
    shares.append((None, 100 * (unseen + unknown * (1 - unseen))))
    percents = [int(share) for _, share in shares]
    by_remainder = sorted(range(len(shares)), key=lambda i: shares[i][1] - percents[i], reverse=True)
    for i in by_remainder[:100 - sum(percents)]:
        percents[i] += 1
    return [{'subject': name, 'conditional_probability': percent}
            for (name, _), percent in zip(shares, percents) if percent]


def predict(history, target):
    past, resume_on = teaching_days(history, target)
    if resume_on:
        return {'status': 'unavailable', 'reason': 'practice', 'resume_on': resume_on.isoformat()}
    weekday = weekday_kind(target)
    matching = [row for row in past if weekday_kind(row.day) == weekday]
    if len(matching) < MIN_MATCHING_DAYS or (target - past[-1].day).days > 21:
        return {}

    keys = {row.day: {n: subject_key(subjects) for n, subjects in row.slots.items()} for row in past}
    subjects_by_day = {day: {subject for slot in slots.values() for subject in slot} for day, slots in keys.items()}
    inactive = inactive_subjects(past, target, subjects_by_day)
    transfer, cases = transfer_cases(past, target, keys, subjects_by_day, inactive)
    transfer_label = next((rows for row in reversed(past) for rows in row.slots.values()
                           if subject_key(rows) == transfer), ()) if transfer else ()
    trials = validate(past, keys, subjects_by_day)
    samples = sample_days(matching, target, keys, subjects_by_day)
    result, adjusted_slots = combine([distribution(samples, model) for model in MODELS], model_weights(trials, weekday),
                                     samples, matching, inactive=inactive, transfer=transfer, cases=cases,
                                     transfer_label=transfer_label)
    recent = [trial for trial in trials if trial.weekday == weekday][-VALIDATION_DAYS:]
    subject_outcomes = [trial.outcomes for trial in recent]
    lessons = []
    for n, slot in result.items():
        presence_outcomes = [(trial.presence_outcomes[n],) for trial in recent if n in trial.presence_outcomes]
        slot_chance = (min(95, round(slot.presence * 100)) if n in adjusted_slots
                       else probability(slot.presence, len(matching), presence_outcomes, step=1 if n == 4 else 5))
        if n == 4 and slot_chance <= 50:
            continue
        lesson = {'n': n, 'slot_probability': slot_chance,
                  'days_present': slot.days_present, 'subject': None, 'probability': None,
                  'subject_options': subject_options(slot)}
        if (n in adjusted_slots and slot.joint_support > 0.50 and subject_key(slot.subjects) == transfer
                and sum(slots.get(n) == missed for _, missed, slots in cases) >= 2):
            lesson['possible_transfer'] = True
        if slot.can_name_subject():
            lesson['subject'] = ' / '.join(slot.subjects)
            lesson['probability'] = min(lesson['slot_probability'], probability(slot.joint_support, len(matching), subject_outcomes))
        lessons.append(lesson)
    if not lessons:
        return {}
    return {'lessons': lessons, 'weekday_samples': len(matching),
            'first_date': matching[0].day.isoformat(), 'last_date': matching[-1].day.isoformat()}


def _open(path):
    return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3)


def history_for(connection, group, before):
    rows = connection.execute("""
        WITH latest AS (
          SELECT date, max(revision) AS revision FROM revisions WHERE date>=? AND date<? GROUP BY date
        )
        SELECT r.date, l.pair_number, l.payload_json
        FROM latest
        JOIN revisions r ON r.date=latest.date AND r.revision=latest.revision
        LEFT JOIN lessons l ON l.revision_id=r.id AND l.group_name=?
        WHERE r.status='parsed' AND (l.id IS NOT NULL OR ? IN (SELECT value FROM json_each(r.groups_json)))
        ORDER BY r.date, l.pair_number, l.id
    """, ((before - timedelta(days=550)).isoformat(), before.isoformat(), group, group))
    dates = {}
    for day, n, raw in rows:
        slots = dates.setdefault(day, {})
        if not raw or not 1 <= n <= 6:
            continue
        payload = json.loads(raw)
        subject = ' '.join((payload.get('subject') or '').split())
        if subject and payload.get('lesson_status') == 'scheduled':
            slots.setdefault(n, {})[subject.casefold()] = subject
    return [SchoolDay(date.fromisoformat(day), {
        n: tuple(sorted(subjects.values(), key=str.casefold)) for n, subjects in slots.items()
    }) for day, slots in dates.items()]


@lru_cache(maxsize=256)
def _forecast(path, group, target, _revision_stamp):
    with closing(_open(path)) as connection:
        # A source already exists for this date. Leave published schedules,
        # confirmed days off and source problems to the official schedule API.
        source = connection.execute('SELECT status FROM revisions WHERE date=? ORDER BY revision DESC LIMIT 1',
                                    (target.isoformat(),)).fetchone()
        if source:
            return {'status': 'published' if source[0] == 'parsed' else 'unavailable'}
        history = history_for(connection, group, target)
    projected = predict(history, target)
    return {'status': 'preliminary' if projected else 'unavailable', **projected}


def schedule_prediction(path, group, target, today):
    if not 0 <= (target - today).days <= HORIZON_DAYS or weekend(target) or not path.is_file():
        return {'status': 'unavailable'}
    with closing(_open(path)) as connection:
        revision_stamp = connection.execute('SELECT max(id) FROM revisions').fetchone()[0]
    return _forecast(path, group.strip().upper(), target, revision_stamp)

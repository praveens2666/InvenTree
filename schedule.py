import csv
import os
import datetime
import json
from typing import List, Dict, Any

# OR-Tools
try:
    from ortools.sat.python import cp_model
except ImportError:
    cp_model = None


def load_staff(csv_path: str) -> List[Dict[str, Any]]:
    staff = []
    with open(csv_path, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            # Expect columns: id,name,location,capacity_per_day
            staff.append({
                'id': row.get('id') or row.get('staff_id') or row.get('ID'),
                'name': row.get('name') or row.get('Name'),
                'location': row.get('location') or row.get('Location') or '',
                'capacity': float(row.get('capacity_per_day') or row.get('capacity') or 1)
            })
    return staff


def build_tasks(mapping: Dict[str, Dict]) -> List[Dict[str, Any]]:
    """Build a flat list of tasks from mapping produced by so.add_parts_to_existing_so

    Expected mapping: {machine: {"order_pk": int, "missing_parts": [..], "target_date": "YYYY-MM-DD", "location": "avadi"}}
    """
    tasks = []
    for machine, info in mapping.items():
        order = info.get('order_pk') or info.get('order') or info.get('order_pk')
        tdate = info.get('target_date') or info.get('target')
        loc = info.get('location') or info.get('loc')
        parts = info.get('missing_parts') or info.get('missing') or []
        for p in parts:
            tasks.append({'machine': machine, 'order': order, 'part': p, 'target_date': tdate, 'location': loc})
    return tasks


def schedule_tasks(staff_list: List[Dict[str, Any]], tasks: List[Dict[str, Any]]):
    if cp_model is None:
        raise RuntimeError('ortools not installed; please pip install ortools')

    model = cp_model.CpModel()

    # Map staff index
    staff_idx = {s['id']: i for i, s in enumerate(staff_list)}
    num_staff = len(staff_list)
    num_tasks = len(tasks)

    # Decision variables: assign[t][s] = 1 if task t assigned to staff s
    assign = {}
    for t in range(num_tasks):
        for s in range(num_staff):
            assign[(t, s)] = model.NewBoolVar(f'a_t{t}_s{s}')

    # Each task must be assigned to exactly one staff whose location matches
    for t, task in enumerate(tasks):
        # allowed staff indices
        allowed = [i for i, s in enumerate(staff_list) if (not task.get('location')) or (s.get('location') and task.get('location').lower() in s.get('location').lower())]
        if not allowed:
            # no staff in matching location: allow any (will appear as unassigned later)
            allowed = list(range(num_staff))
        model.Add(sum(assign[(t, s)] for s in allowed) == 1)

    # Capacity constraints per staff: total assigned tasks per staff <= capacity (rounded)
    for s_idx, s in enumerate(staff_list):
        cap = int(round(s.get('capacity', 1)))
        model.Add(sum(assign[(t, s_idx)] for t in range(num_tasks)) <= cap)

    # Simple objective: balance load (minimize max assigned)
    loads = [model.NewIntVar(0, num_tasks, f'load_s{s}') for s in range(num_staff)]
    for s in range(num_staff):
        model.Add(loads[s] == sum(assign[(t, s)] for t in range(num_tasks)))
    max_load = model.NewIntVar(0, num_tasks, 'max_load')
    model.AddMaxEquality(max_load, loads)
    model.Minimize(max_load)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    solver.parameters.num_search_workers = 8

    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError('No feasible schedule found')

    schedule = []
    for t, task in enumerate(tasks):
        for s_idx in range(num_staff):
            if solver.Value(assign[(t, s_idx)]) == 1:
                schedule.append({'task': task, 'staff': staff_list[s_idx]})
                break
    return schedule


def schedule_tasks_multi_day(staff_list: List[Dict[str, Any]], tasks: List[Dict[str, Any]]):
    """Schedule tasks across multiple days respecting each task target_date.

    Assumptions:
    - each task requires one unit of staff time (one staff for one day)
    - staff capacity is number of tasks per day
    - tasks without target_date may be scheduled up to the max target_date
    """
    if cp_model is None:
        raise RuntimeError('ortools not installed; please pip install ortools')

    # Build day horizon from today to max target_date across tasks (with padding)
    today = datetime.date.today()
    def parse_date(d):
        if not d:
            return None
        try:
            return datetime.date.fromisoformat(str(d))
        except Exception:
            return None

    deadlines = [parse_date(t.get('target_date')) for t in tasks]
    max_deadline = max([d for d in deadlines if d is not None], default=None)
    if max_deadline is None:
        # fallback to a single-day horizon (today)
        max_deadline = today

    # allow scheduling after the latest target date by a padding (default 7 days)
    pad_days = int(os.getenv('SCHEDULE_PADDING_DAYS', '7'))
    horizon_end = max_deadline + datetime.timedelta(days=pad_days)
    horizon_days = (horizon_end - today).days + 1
    if horizon_days <= 0:
        horizon_days = 1

    model = cp_model.CpModel()
    num_staff = len(staff_list)
    num_tasks = len(tasks)

    # x[t,s,d] boolean: task t assigned to staff s on day index d (0 = today)
    x = {}
    for t in range(num_tasks):
        for s in range(num_staff):
            for d in range(horizon_days):
                x[(t, s, d)] = model.NewBoolVar(f'x_t{t}_s{s}_d{d}')

    # Each task must be assigned exactly once on a day <= deadline
    for t, task in enumerate(tasks):
        td = parse_date(task.get('target_date'))
        if td is None:
            start_day = 0
        else:
            # schedule only on or after the target date; if target_date is past, allow today
            start_day = max((td - today).days, 0)
            if start_day >= horizon_days:
                # extend horizon to include start_day
                # this is a safety fallback; cap to last day
                start_day = horizon_days - 1

        # allowed staff indices based on location
        allowed_staff = [i for i, s in enumerate(staff_list) if (not task.get('location')) or (s.get('location') and task.get('location').lower() in s.get('location').lower())]
        if not allowed_staff:
            allowed_staff = list(range(num_staff))

        # enforce assignment on or after start_day
        model.Add(sum(x[(t, s, d)] for s in allowed_staff for d in range(start_day, horizon_days)) == 1)

    # Staff capacity per day
    for s_idx, s in enumerate(staff_list):
        cap = int(round(s.get('capacity', 1)))
        for d in range(horizon_days):
            model.Add(sum(x[(t, s_idx, d)] for t in range(num_tasks)) <= cap)

    # Objective: minimize maximum assigned day (schedule as early as possible)
    last_assigned = model.NewIntVar(0, horizon_days - 1, 'last_assigned')
    # For each task, if assigned at day d then that contributes to last_assigned
    for t in range(num_tasks):
        # mapped day var for task t
        day_var = model.NewIntVar(0, horizon_days - 1, f'day_t{t}')
        # link day_var with x variables
        # day_var == sum(d * x[t,s,d]) for all s,d
        coeffs = []
        vars_ = []
        for s in range(num_staff):
            for d in range(horizon_days):
                vars_.append(x[(t, s, d)])
                coeffs.append(d)
        # create intermediate linear expression: sum(d * x) == day_var
        model.Add(sum(coeffs[i] * vars_[i] for i in range(len(vars_))) == day_var)
        model.Add(last_assigned >= day_var)

    model.Minimize(last_assigned)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20
    solver.parameters.num_search_workers = 8

    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError('No feasible multi-day schedule found')

    schedule = []
    for t, task in enumerate(tasks):
        assigned = False
        for s_idx in range(num_staff):
            for d in range(horizon_days):
                if solver.Value(x[(t, s_idx, d)]) == 1:
                    assigned_date = today + datetime.timedelta(days=d)
                    schedule.append({'task': task, 'staff': staff_list[s_idx], 'date': assigned_date.isoformat()})
                    assigned = True
                    break
            if assigned:
                break
    return schedule


def write_schedule_csv(schedule: List[Dict[str, Any]], out_path: str):
    """Write schedule list to CSV with columns: machine,order,part,target_date,location,staff_id,staff_name,staff_location,assigned_date"""
    with open(out_path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(['machine', 'order', 'part', 'target_date', 'location', 'staff_id', 'staff_name', 'staff_location', 'assigned_date'])
        for row in schedule:
            task = row.get('task', {})
            staff = row.get('staff', {})
            assigned_date = row.get('date') or ''
            writer.writerow([
                task.get('machine'),
                task.get('order'),
                task.get('part'),
                task.get('target_date'),
                task.get('location'),
                staff.get('id'),
                staff.get('name'),
                staff.get('location'),
                assigned_date,
            ])


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        print('Usage: python schedule.py staff_dataset.csv mapping.json')
        sys.exit(1)
    staff_csv = sys.argv[1]
    mapping_file = sys.argv[2]
    staff = load_staff(staff_csv)
    with open(mapping_file, 'r', encoding='utf-8') as fh:
        mapping = json.load(fh)
    tasks = build_tasks(mapping)
    # CLI flags via env or args: support --multi-day and --out-csv via argv
    use_multi = '--multi-day' in sys.argv
    out_csv = None
    if '--out-csv' in sys.argv:
        try:
            idx = sys.argv.index('--out-csv')
            out_csv = sys.argv[idx + 1]
        except Exception:
            out_csv = None

    if use_multi:
        sched = schedule_tasks_multi_day(staff, tasks)
    else:
        sched = schedule_tasks(staff, tasks)

    print(json.dumps(sched, indent=2))
    if out_csv and sched:
        write_schedule_csv(sched, out_csv)
        print(f'Wrote schedule to {out_csv}')

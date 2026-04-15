import argparse
import csv
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape as xml_escape


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate commit blocks SVG from daily timeline CSV (SVG-only)."
    )
    parser.add_argument("--input", default="stats_timeline_daily.csv", help="Path to daily timeline CSV")
    parser.add_argument("--svg-output", default="commit_blocks.svg", help="Output SVG file")
    parser.add_argument(
        "--output",
        default=None,
        help="Deprecated alias for --svg-output. Kept for backward compatibility.",
    )
    parser.add_argument(
        "--account",
        default="__combined__",
        help="Account name in CSV (for example __combined__ or AhmedAbubaker98)",
    )
    parser.add_argument("--title", default="Commit History", help="Chart title")
    return parser.parse_args()


def load_daily_counts(csv_path, account):
    # Rolling one-year window based on current UTC date.
    today = datetime.now(timezone.utc).date()
    one_year_ago = today - timedelta(days=365)
    
    counts = {}

    with open(csv_path, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row.get("account") != account:
                continue

            day = (row.get("date") or "").strip()
            if not day:
                continue

            try:
                day_date = datetime.strptime(day, "%Y-%m-%d").date()
                # Only include data from past year
                if day_date >= one_year_ago:
                    counts[day] = int((row.get("commits") or "0").strip())
            except ValueError:
                continue

    return counts


def parse_day(day_text):
    return datetime.strptime(day_text, "%Y-%m-%d").date()


def align_to_monday(day):
    return day - timedelta(days=day.weekday())


def align_to_sunday(day):
    return day + timedelta(days=(6 - day.weekday()))


def percentile(sorted_values, ratio):
    if not sorted_values:
        return 0

    index = int(round((len(sorted_values) - 1) * ratio))
    index = max(0, min(index, len(sorted_values) - 1))
    return sorted_values[index]


def build_thresholds(counts):
    non_zero = sorted(value for value in counts.values() if value > 0)
    if not non_zero:
        return [0, 0, 0]

    return [
        percentile(non_zero, 0.25),
        percentile(non_zero, 0.50),
        percentile(non_zero, 0.75),
    ]


def level_for_count(count, thresholds):
    if count <= 0:
        return 0
    if count <= thresholds[0]:
        return 1
    if count <= thresholds[1]:
        return 2
    if count <= thresholds[2]:
        return 3
    return 4


def longest_streak(day_range, counts):
    current = 0
    best = 0

    for day in day_range:
        if counts.get(day.isoformat(), 0) > 0:
            current += 1
            best = max(best, current)
        else:
            current = 0

    return best


def build_payload(title, account, counts):
    if not counts:
        raise ValueError(
            f"No rows found for account '{account}'. Check --account and input CSV."
        )

    all_days = sorted(parse_day(day) for day in counts)
    start_day = align_to_monday(all_days[0])
    end_day = align_to_sunday(all_days[-1])

    day_range = []
    cursor = start_day
    while cursor <= end_day:
        day_range.append(cursor)
        cursor += timedelta(days=1)

    thresholds = build_thresholds(counts)
    total_weeks = ((end_day - start_day).days // 7) + 1

    cells = []
    month_labels = []
    seen_months = set()

    for index, day in enumerate(day_range):
        day_key = day.isoformat()
        week = (day - start_day).days // 7

        cells.append(
            {
                "date": day_key,
                "count": counts.get(day_key, 0),
                "level": level_for_count(counts.get(day_key, 0), thresholds),
                "week": week,
                "weekday": day.weekday(),
                "index": index,
            }
        )

        month_key = day.strftime("%Y-%m")
        if day.day == 1 and month_key not in seen_months:
            month_labels.append({"label": day.strftime("%b"), "week": week})
            seen_months.add(month_key)

    return {
        "title": title,
        "account": account,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "period": {"from": all_days[0].isoformat(), "to": all_days[-1].isoformat()},
        "summary": {
            "total_commits": sum(counts.values()),
            "active_days": sum(1 for value in counts.values() if value > 0),
            "longest_streak_days": longest_streak(day_range, counts),
            "total_weeks": total_weeks,
        },
        "legend_thresholds": {
            "low": thresholds[0],
            "medium": thresholds[1],
            "high": thresholds[2],
        },
        "month_labels": month_labels,
        "cells": cells,
    }


def render_svg(payload):
    cell = 12
    gap = 3
    left = 58
    top = 64
    weeks = payload["summary"]["total_weeks"]
    grid_width = weeks * (cell + gap) - gap
    grid_height = 7 * (cell + gap) - gap

    width = max(920, left + grid_width + 26)
    height = top + grid_height + 78

    lines = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{xml_escape(payload["title"])}">'
    )
    lines.append("<defs>")
    lines.append("<linearGradient id=\"bg\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"1\">")
    lines.append("<stop offset=\"0%\" stop-color=\"#161c20\"/>")
    lines.append("<stop offset=\"100%\" stop-color=\"#0C0E10\"/>")
    lines.append("</linearGradient>")
    lines.append("<style>")
    lines.append(".title{fill:#C5A96D;font:700 22px 'Segoe UI',sans-serif}")
    lines.append(".month{fill:#aaaaaa;font:11px 'Consolas',monospace}.day{fill:#aaaaaa;font:11px 'Consolas',monospace}")
    lines.append(".legend{fill:#aaaaaa;font:11px 'Consolas',monospace}")
    lines.append(".panel{fill:rgba(22,28,32,.82);stroke:rgba(165,149,109,.25);stroke-width:1}")
    lines.append(".cell{opacity:0;transform-box:fill-box;transform-origin:center;animation:pop .52s cubic-bezier(.2,.8,.2,1) forwards;animation-delay:calc(var(--i)*7ms)}")
    lines.append(".l0{fill:#2a2420}.l1{fill:#d4a574}.l2{fill:#c5a96d}.l3{fill:#a68550}.l4{fill:#daa522}")
    lines.append(".annotation-main{fill:#C5A96D;font:700 21px 'Segoe UI',sans-serif;opacity:.24;pointer-events:none}")
    lines.append(".annotation-secondary{fill:#C5A96D;font:700 24px 'Segoe UI',sans-serif;opacity:.20;pointer-events:none}")
    lines.append("@keyframes pop{to{opacity:1;transform:scale(1)}}")
    lines.append("</style>")
    lines.append("</defs>")

    lines.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="url(#bg)"/>')
    lines.append(f'<rect class="panel" x="14" y="14" rx="16" width="{width - 28}" height="{height - 28}"/>')

    title = xml_escape(payload["title"])
    lines.append(f'<text class="title" x="28" y="44">{title}</text>')

    for month in payload["month_labels"]:
        x = left + month["week"] * (cell + gap)
        label = xml_escape(month["label"])
        lines.append(f'<text class="month" x="{x}" y="58">{label}</text>')

    day_labels = {0: "Mon", 3: "Thu", 6: "Sun"}
    for row, day_name in day_labels.items():
        y = top + row * (cell + gap) + 10
        lines.append(f'<text class="day" x="22" y="{y}">{day_name}</text>')

    for cell_item in payload["cells"]:
        x = left + cell_item["week"] * (cell + gap)
        y = top + cell_item["weekday"] * (cell + gap)
        level = int(cell_item["level"])
        tooltip = xml_escape(f"{cell_item['date']}: {cell_item['count']} commits")
        lines.append(
            f'<rect class="cell l{level}" x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" style="--i:{cell_item["index"]};">'
            f'<title>{tooltip}</title></rect>'
        )

    legend_y = top + grid_height + 28
    legend_items = [
        (0, "No commits"),
        (1, f"Low <= {payload['legend_thresholds']['low']}"),
        (2, f"Mid <= {payload['legend_thresholds']['medium']}"),
        (3, f"High <= {payload['legend_thresholds']['high']}"),
        (4, f"Peak > {payload['legend_thresholds']['high']}"),
    ]

    legend_x = 28
    for level, text in legend_items:
        lines.append(f'<rect class="l{level}" x="{legend_x}" y="{legend_y - 10}" width="10" height="10" rx="2"/>')
        lines.append(f'<text class="legend" x="{legend_x + 16}" y="{legend_y}">{xml_escape(text)}</text>')
        legend_x += 160

    def pick_anchor(start_day, end_day, prefer_zero=False):
        ranged = []
        for item in payload["cells"]:
            item_day = datetime.strptime(item["date"], "%Y-%m-%d").date()
            if start_day <= item_day <= end_day:
                ranged.append(item)

        if not ranged:
            return None

        if prefer_zero:
            zero_cells = [item for item in ranged if item["count"] == 0]
            if len(zero_cells) >= 4:
                return zero_cells[len(zero_cells) // 2]

        return ranged[len(ranged) // 2]

    def window_anchor(month_start, day_start, month_end, day_end, prefer_zero=False):
        # Try current period end year first, then previous year so windows stay pinned
        # as the rolling 1-year window crosses year boundaries.
        candidate_years = [period_end.year, period_end.year - 1]
        for year in candidate_years:
            start_day = datetime(year, month_start, day_start).date()
            end_day = datetime(year, month_end, day_end).date()

            # Skip windows completely outside visible range.
            if end_day < period_start or start_day > period_end:
                continue

            clipped_start = max(start_day, period_start)
            clipped_end = min(end_day, period_end)
            anchor = pick_anchor(clipped_start, clipped_end, prefer_zero=prefer_zero)
            if anchor is not None:
                return anchor

        return None

    period_start = datetime.strptime(payload["period"]["from"], "%Y-%m-%d").date()
    period_end = datetime.strptime(payload["period"]["to"], "%Y-%m-%d").date()

    # Main annotation: National Holiday over Mar 1 -> Apr 15 (calendar pinned).
    main_anchor = window_anchor(3, 1, 4, 15, prefer_zero=True)
    if main_anchor:
        main_x = left + main_anchor["week"] * (cell + gap) + (cell / 2)
        main_y = top + (grid_height / 2)
        lines.append(
            f'<text class="annotation-main" x="{main_x:.0f}" y="{main_y:.0f}" text-anchor="middle" dominant-baseline="middle" transform="rotate(-90 {main_x:.0f} {main_y:.0f})">'
            f'<tspan x="{main_x:.0f}" dy="-0.35em">NATIONAL</tspan>'
            f'<tspan x="{main_x:.0f}" dy="1.1em">HOLIDAY</tspan>'
            f'</text>'
        )

    # Secondary annotation: Sabbatical in Jul 1 -> Sep 30 (calendar pinned).
    secondary_anchor = window_anchor(7, 1, 9, 30, prefer_zero=True)
    if secondary_anchor:
        sec_x = left + secondary_anchor["week"] * (cell + gap) + (cell / 2)
        sec_y = top + (grid_height / 2)
        lines.append(
            f'<text class="annotation-secondary" x="{sec_x:.0f}" y="{sec_y:.0f}" text-anchor="middle" dominant-baseline="middle" transform="rotate(-90 {sec_x:.0f} {sec_y:.0f})">SABBATICAL</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


def main():
    args = parse_args()
    svg_output = args.output if args.output else args.svg_output

    counts = load_daily_counts(args.input, args.account)
    payload = build_payload(args.title, args.account, counts)

    with open(svg_output, "w", encoding="utf-8") as file:
        file.write(render_svg(payload))

    print(f"Wrote commit blocks SVG to {svg_output}")


if __name__ == "__main__":
    main()

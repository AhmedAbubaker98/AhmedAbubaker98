import argparse
import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
ASSETS_DIR = REPO_ROOT / "assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def _resolve_path(path_value):
    path = Path(path_value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate commit graph SVG from timeline CSV (SVG-only)."
    )
    parser.add_argument("--input", default="assets/stats_timeline_monthly.csv", help="Input timeline CSV")
    parser.add_argument("--svg-output", default="assets/commit_graph.svg", help="Output SVG file")
    parser.add_argument(
        "--output",
        default=None,
        help="Deprecated alias for --svg-output. Kept for backward compatibility.",
    )
    parser.add_argument("--account", default="__combined__", help="Account name in CSV")
    parser.add_argument(
        "--granularity",
        choices=["daily", "monthly"],
        default="monthly",
        help="Timeline granularity",
    )
    parser.add_argument("--title", default="Commit Graph", help="Chart title")
    return parser.parse_args()


def load_series(csv_path, account, granularity):
    x_field = "date" if granularity == "daily" else "month"
    
    # Calculate one year ago from current UTC date.
    today = datetime.now(timezone.utc).date()
    one_year_ago = today - timedelta(days=365)

    points = []
    with open(csv_path, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row.get("account") != account:
                continue

            x_value = (row.get(x_field) or "").strip()
            if not x_value:
                continue

            try:
                y_value = int((row.get("commits") or "0").strip())
            except ValueError:
                continue

            # Parse date and check if it's within past year
            if granularity == "daily":
                point_date = datetime.strptime(x_value, "%Y-%m-%d").date()
            else:
                # For monthly, use the first day of the month
                point_date = datetime.strptime(x_value, "%Y-%m").date()
            
            if point_date >= one_year_ago:
                points.append({"x": x_value, "y": y_value})

    points.sort(key=lambda item: item["x"])
    return points


def moving_average(points, window):
    if window <= 1:
        return [{"x": item["x"], "y": float(item["y"])} for item in points]

    result = []
    rolling_sum = 0.0
    values = [float(item["y"]) for item in points]

    for index, item in enumerate(points):
        rolling_sum += values[index]
        if index >= window:
            rolling_sum -= values[index - window]

        denominator = min(index + 1, window)
        result.append({"x": item["x"], "y": round(rolling_sum / denominator, 2)})

    return result


def build_payload(title, account, granularity, points):
    if not points:
        raise ValueError(
            f"No rows found for account '{account}'. Check --account and --input values."
        )

    smooth_window = 3 if granularity == "monthly" else 7
    smoothed = moving_average(points, smooth_window)
    peak_point = max(points, key=lambda item: item["y"])

    return {
        "title": title,
        "account": account,
        "granularity": granularity,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "summary": {
            "points": len(points),
            "total_commits": sum(item["y"] for item in points),
            "peak_label": peak_point["x"],
            "peak_value": peak_point["y"],
            "smoothing_window": smooth_window,
        },
        "series": points,
        "smoothed": smoothed,
    }


def _coords_from_points(points, x_scale, y_scale):
    return [
        (x_scale(index, len(points)), y_scale(point["y"]))
        for index, point in enumerate(points)
    ]


def line_path(points, x_scale, y_scale):
    """Generate a Catmull-Rom to cubic Bezier smooth path."""
    if not points:
        return ""

    coords = _coords_from_points(points, x_scale, y_scale)
    if len(coords) == 1:
        x0, y0 = coords[0]
        return f"M {x0:.2f} {y0:.2f}"

    commands = [f"M {coords[0][0]:.2f} {coords[0][1]:.2f}"]
    for i in range(len(coords) - 1):
        p0 = coords[i - 1] if i > 0 else coords[i]
        p1 = coords[i]
        p2 = coords[i + 1]
        p3 = coords[i + 2] if i + 2 < len(coords) else coords[i + 1]

        c1x = p1[0] + (p2[0] - p0[0]) / 6.0
        c1y = p1[1] + (p2[1] - p0[1]) / 6.0
        c2x = p2[0] - (p3[0] - p1[0]) / 6.0
        c2y = p2[1] - (p3[1] - p1[1]) / 6.0
        commands.append(
            f"C {c1x:.2f} {c1y:.2f} {c2x:.2f} {c2y:.2f} {p2[0]:.2f} {p2[1]:.2f}"
        )

    return " ".join(commands)


def area_path(points, x_scale, y_scale, base_y):
    if not points:
        return ""

    coords = _coords_from_points(points, x_scale, y_scale)
    first_x = coords[0][0]
    last_x = coords[-1][0]

    commands = [f"M {first_x:.2f} {base_y:.2f}", f"L {first_x:.2f} {coords[0][1]:.2f}"]
    for i in range(len(coords) - 1):
        p0 = coords[i - 1] if i > 0 else coords[i]
        p1 = coords[i]
        p2 = coords[i + 1]
        p3 = coords[i + 2] if i + 2 < len(coords) else coords[i + 1]

        c1x = p1[0] + (p2[0] - p0[0]) / 6.0
        c1y = p1[1] + (p2[1] - p0[1]) / 6.0
        c2x = p2[0] - (p3[0] - p1[0]) / 6.0
        c2y = p2[1] - (p3[1] - p1[1]) / 6.0
        commands.append(
            f"C {c1x:.2f} {c1y:.2f} {c2x:.2f} {c2y:.2f} {p2[0]:.2f} {p2[1]:.2f}"
        )

    commands.append(f"L {last_x:.2f} {base_y:.2f}")
    commands.append("Z")
    return " ".join(commands)


def render_svg(payload):
    width = 1360
    height = 470
    sidebar_width = 200
    pad_left = 70 + sidebar_width
    pad_right = 30
    pad_top = 25
    pad_bottom = 54

    x_width = width - pad_left - pad_right
    y_height = height - pad_top - pad_bottom

    points = payload["series"]
    y_max = max(max(point["y"] for point in points), 1)

    def y_scale(value):
        return height - pad_bottom - (value / y_max) * y_height

    def x_scale(index, length):
        return pad_left + (index / max(length - 1, 1)) * x_width

    lines = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{xml_escape(payload["title"])}">'
    )
    lines.append("<defs>")
    lines.append("<linearGradient id=\"bg\" x1=\"0\" y1=\"0\" x2=\"1\" y2=\"1\">")
    lines.append("<stop offset=\"0%\" stop-color=\"#161c20\"/>")
    lines.append("<stop offset=\"100%\" stop-color=\"#0C0E10\"/>")
    lines.append("</linearGradient>")
    lines.append("<linearGradient id=\"area\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">")
    lines.append("<stop offset=\"0%\" stop-color=\"#C5A96D\" stop-opacity=\"0.5\"/>")
    lines.append("<stop offset=\"100%\" stop-color=\"#C5A96D\" stop-opacity=\"0.03\"/>")
    lines.append("</linearGradient>")
    lines.append("<style>")
    lines.append(".title{fill:#C5A96D;font:700 22px 'Segoe UI',sans-serif}.sub{fill:#aaaaaa;font:12px 'Consolas',monospace}")
    lines.append(".axis{stroke:rgba(165,149,109,.3);stroke-dasharray:3 8;stroke-width:1}.tick{fill:#aaaaaa;font:11px 'Consolas',monospace}")
    lines.append(".area{fill:url(#area)}.line{stroke:#C5A96D;stroke-width:3;fill:none;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:1000;stroke-dashoffset:1000;animation:draw 1.9s ease forwards}")
    lines.append(".dot{fill:#daa522;opacity:0;animation:pop .35s ease forwards;animation-delay:calc(var(--i)*14ms + 220ms)}")
    lines.append(".sidebar-text{fill:#aaaaaa;font:12px 'Consolas',monospace}.sidebar-label{fill:#C5A96D;font:12px 'Segoe UI',sans-serif;font-weight:bold}")
    lines.append(".icon{stroke:#C5A96D;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}")
    lines.append(".icon-fill{fill:#C5A96D;stroke:none}")
    lines.append("@keyframes draw{to{stroke-dashoffset:0}}@keyframes pop{to{opacity:1}}")
    lines.append("</style>")
    lines.append("</defs>")

    lines.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="url(#bg)"/>')

    # Add sidebar background
    lines.append(f'<rect x="0" y="0" width="{sidebar_width + 70}" height="{height}" fill="rgba(22,28,32,.4)"/>')

    total_contrib = payload["summary"]["total_commits"]
    year_label = payload["series"][-1]["x"][:4]

    # Sidebar layout
    sidebar_x = 28
    icon_x = sidebar_x
    text_x = sidebar_x + 30

    sec1_y = 96
    sec2_y = 188
    sec3_y = 280

    # GH icon (octocat-style silhouette)
    gh_cx = icon_x + 8
    gh_cy = sec1_y - 8
    lines.append(
        f'<path class="icon-fill" d="'
        f'M {gh_cx:.1f} {gh_cy + 7:.1f} '
        f'C {gh_cx - 5:.1f} {gh_cy + 7:.1f} {gh_cx - 8:.1f} {gh_cy + 3:.1f} {gh_cx - 8:.1f} {gh_cy - 1:.1f} '
        f'C {gh_cx - 8:.1f} {gh_cy - 3:.1f} {gh_cx - 7:.1f} {gh_cy - 5:.1f} {gh_cx - 5.5:.1f} {gh_cy - 6.5:.1f} '
        f'L {gh_cx - 4.5:.1f} {gh_cy - 10:.1f} '
        f'L {gh_cx - 1.6:.1f} {gh_cy - 7.4:.1f} '
        f'C {gh_cx - 0.5:.1f} {gh_cy - 7.8:.1f} {gh_cx + 0.5:.1f} {gh_cy - 7.8:.1f} {gh_cx + 1.6:.1f} {gh_cy - 7.4:.1f} '
        f'L {gh_cx + 4.5:.1f} {gh_cy - 10:.1f} '
        f'L {gh_cx + 5.5:.1f} {gh_cy - 6.5:.1f} '
        f'C {gh_cx + 7:.1f} {gh_cy - 5:.1f} {gh_cx + 8:.1f} {gh_cy - 3:.1f} {gh_cx + 8:.1f} {gh_cy - 1:.1f} '
        f'C {gh_cx + 8:.1f} {gh_cy + 3:.1f} {gh_cx + 5:.1f} {gh_cy + 7:.1f} {gh_cx:.1f} {gh_cy + 7:.1f} Z"/>'
    )
    lines.append(f'<text class="sidebar-label" x="{text_x}" y="{sec1_y - 10}">GitHub</text>')
    lines.append(f'<text class="sidebar-text" x="{text_x}" y="{sec1_y + 10}">{total_contrib} contributions in {year_label}</text>')

    # Clock icon
    lines.append(f'<circle class="icon" cx="{icon_x + 8}" cy="{sec2_y - 8}" r="8"/>')
    lines.append(f'<line class="icon" x1="{icon_x + 8}" y1="{sec2_y - 8}" x2="{icon_x + 8}" y2="{sec2_y - 12}"/>')
    lines.append(f'<line class="icon" x1="{icon_x + 8}" y1="{sec2_y - 8}" x2="{icon_x + 12}" y2="{sec2_y - 8}"/>')
    lines.append(f'<text class="sidebar-label" x="{text_x}" y="{sec2_y - 10}">Joined GitHub</text>')
    lines.append(f'<text class="sidebar-text" x="{text_x}" y="{sec2_y + 10}">2021</text>')

    # Email icon
    lines.append(f'<rect class="icon" x="{icon_x}" y="{sec3_y - 16}" width="16" height="12" rx="2"/>')
    lines.append(f'<polyline class="icon" points="{icon_x},{sec3_y - 16} {icon_x + 8},{sec3_y - 9} {icon_x + 16},{sec3_y - 16}"/>')
    lines.append(f'<text class="sidebar-label" x="{text_x}" y="{sec3_y - 10}">Email</text>')
    lines.append(f'<text class="sidebar-text" x="{text_x}" y="{sec3_y + 8}">AhmedElagibWork</text>')
    lines.append(f'<text class="sidebar-text" x="{text_x}" y="{sec3_y + 24}">@Gmail.com</text>')

    title = xml_escape(payload["title"])
    lines.append(f'<text class="title" x="{pad_left}" y="36">{title}</text>')

    y_ticks = 5
    for tick in range(y_ticks + 1):
        value = (y_max / y_ticks) * tick
        y = y_scale(value)
        lines.append(
            f'<line class="axis" x1="{pad_left}" x2="{width - pad_right}" y1="{y:.2f}" y2="{y:.2f}"/>'
        )
        lines.append(
            f'<text class="tick" x="{pad_left - 10}" y="{y + 4:.2f}" text-anchor="end">{int(round(value))}</text>'
        )

    x_tick_target = 8
    x_step = max(1, len(points) // x_tick_target)
    month_abbr = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    for index in range(0, len(points), x_step):
        x = x_scale(index, len(points))
        point_str = points[index]["x"]
        # Extract month from YYYY-MM format
        if len(point_str) >= 7 and point_str[4] == "-":
            month_num = int(point_str[5:7]) - 1
            label = xml_escape(month_abbr[month_num] if 0 <= month_num < 12 else point_str)
        else:
            label = xml_escape(point_str)
        lines.append(f'<text class="tick" x="{x:.2f}" y="{height - 18}" text-anchor="middle">{label}</text>')

    if (len(points) - 1) % x_step != 0:
        x = x_scale(len(points) - 1, len(points))
        point_str = points[-1]["x"]
        if len(point_str) >= 7 and point_str[4] == "-":
            month_num = int(point_str[5:7]) - 1
            label = xml_escape(month_abbr[month_num] if 0 <= month_num < 12 else point_str)
        else:
            label = xml_escape(point_str)
        lines.append(f'<text class="tick" x="{x:.2f}" y="{height - 18}" text-anchor="middle">{label}</text>')

    lines.append(f'<path class="area" d="{area_path(points, x_scale, y_scale, height - pad_bottom)}"/>')
    lines.append(f'<path class="line" d="{line_path(points, x_scale, y_scale)}" pathLength="1000"/>')

    for index, point in enumerate(points):
        x = x_scale(index, len(points))
        y = y_scale(point["y"])
        tip = xml_escape(f"{point['x']}: {point['y']} commits")
        lines.append(
            f'<circle class="dot" cx="{x:.2f}" cy="{y:.2f}" r="3.4" style="--i:{index};"><title>{tip}</title></circle>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


def main():
    args = parse_args()
    input_path = _resolve_path(args.input)
    svg_output = _resolve_path(args.output if args.output else args.svg_output)

    points = load_series(str(input_path), args.account, args.granularity)
    payload = build_payload(args.title, args.account, args.granularity, points)

    svg_output.parent.mkdir(parents=True, exist_ok=True)
    with open(svg_output, "w", encoding="utf-8") as file:
        file.write(render_svg(payload))

    print(f"Wrote commit graph SVG to {svg_output}")


if __name__ == "__main__":
    main()

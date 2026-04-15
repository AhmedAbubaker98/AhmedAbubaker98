import argparse
import subprocess
import sys


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run both visualization generators in one command."
    )
    parser.add_argument(
        "--account",
        default="__combined__",
        help="Account selector passed to both visualization scripts",
    )
    parser.add_argument(
        "--blocks-svg-output",
        default="commit_blocks.svg",
        help="Output file for commit blocks SVG",
    )
    parser.add_argument(
        "--graph-svg-output",
        default="commit_graph.svg",
        help="Output file for commit graph SVG",
    )
    parser.add_argument(
        "--graph-granularity",
        choices=["daily", "monthly"],
        default="monthly",
        help="Granularity for animated graph",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    blocks_cmd = [
        sys.executable,
        "generate_commit_blocks.py",
        "--account",
        args.account,
        "--svg-output",
        args.blocks_svg_output,
    ]

    graph_cmd = [
        sys.executable,
        "generate_commit_graph.py",
        "--account",
        args.account,
        "--granularity",
        args.graph_granularity,
        "--svg-output",
        args.graph_svg_output,
    ]

    print("Running commit blocks generator...")
    subprocess.run(blocks_cmd, check=True)

    print("Running commit graph generator...")
    subprocess.run(graph_cmd, check=True)

    print("All visualizations generated successfully.")


if __name__ == "__main__":
    main()

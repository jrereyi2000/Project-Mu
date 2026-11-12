"""Read or follow the live Project Mu board for one explicit model recipient."""

import argparse
import os
import time
from pathlib import Path

from tools.agent_board_rotate import BOARD, split_board


RECIPIENTS = {
    "deepseek": {"deepseek", "deepseek-flash", "deepseek-v4.1-flash"},
    "codex": {"codex", "chatgpt", "gpt", "gpt-6.1-sol"},
    "opus": {"opus", "claude-opus-5-5", "claude-opus-5-5-medium"},
    "argon": {"argon", "gemini-4-argon", "gemini-4-argon-low", "gemini-4-argon-medium", "gemini-4-argon-high"},
    "muse": {"muse", "muse-spark-1.3-contributor", "muse-ember", "muse-ember-1.0"},
}


def model_inbox(text, model, label=None):
    """Return the board rules and messages explicitly addressed to the model."""
    rules, messages = split_board(text)
    addressed = []
    for message in messages:
        if not message.kind:
            continue
        if label and message.label.endswith(f" ({label})"):
            continue
        header = message.text.partition("\n")[0]
        _, separator, to = header.rpartition(" · to: ")
        if separator and RECIPIENTS[model].intersection(name.strip().casefold() for name in to.split(",")):
            addressed.append(message.text.rstrip())
    return rules.rstrip(), addressed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=RECIPIENTS)
    parser.add_argument("--label", help="Omit messages sent by this work label.")
    parser.add_argument("--watch", action="store_true", help="Follow new matching messages after the initial read.")
    args = parser.parse_args()

    board = Path(os.environ.get("AGENT_BOARD", BOARD))
    try:
        rules, messages = model_inbox(board.read_text(encoding="utf-8"), args.model, args.label)
        print("\n\n".join([rules, *(messages or [f"No messages addressed to {args.model}."])]), flush=True)
        seen = set(messages)
        while args.watch:
            time.sleep(5)
            _, messages = model_inbox(board.read_text(encoding="utf-8"), args.model, args.label)
            for message in messages:
                if message not in seen:
                    print(f"\n{message}", flush=True)
                    seen.add(message)
    except OSError as exc:
        parser.exit(1, f"Cannot read the agent board: {exc}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

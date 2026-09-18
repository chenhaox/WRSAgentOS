"""Connect to a running Mock TTS node; share WRS_AGENT_TOKEN with its launcher."""

import argparse

from wrs_agent import connect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="tcp/127.0.0.1:7447")
    parser.add_argument("--env-id", default="arm01")
    args = parser.parse_args()
    with connect(args.endpoint, env_id=args.env_id, bindings="configs/tts.toml") as system:
        speech = system.action("speak", text="Connected to the existing node")
        print(speech.wait().state)
    # Closing this connection leaves the node running.


if __name__ == "__main__":
    main()

import argparse
import importlib

from .artifacts import read, write


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--parameters", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--replicate-result")
    args = parser.parse_args()
    adapter = importlib.import_module(args.adapter)
    parameters, data = read(args.parameters), read(args.data)
    if args.replicate_result:
        result = {"verified": adapter.replicate(parameters, data, read(args.replicate_result)) is True}
    else:
        result = adapter.evaluate(parameters, data)
    write(args.output, result)


if __name__ == "__main__":
    main()

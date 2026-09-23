"""Command-line entry point for source-validated ingestion."""
import argparse

from .pipeline import run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest",default="config/sources.json")
    parser.add_argument("--settings",default="config/ingestion.json")
    parser.add_argument("--output-dir",help="New directory under data/processed; existing directories are never overwritten")
    args=parser.parse_args()
    directory,report=run(args.manifest,args.settings,args.output_dir)
    print(f"Complete: {directory}")
    print(f"Transactions: {report['tables']['transactions.jsonl']['rows']}; products: {report['tables']['products.jsonl']['rows']}")


if __name__=="__main__":
    main()

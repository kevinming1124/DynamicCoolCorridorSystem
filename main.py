"""Run the Dynamic Cool Corridor System model pipeline."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Protocol, Sequence

from models._template import ModelTemplate
from utils import CampusGraph, ModelInput, ModelOutput, load_campus_graph


PROJECT_ROOT = Path(__file__).parent
DEFAULT_DATASET = PROJECT_ROOT / "dataset" / "tiny_campus_block"


class PipelineStage(Protocol):
    """Minimum interface required for a pipeline model."""

    def predict(self, model_input: ModelInput) -> ModelOutput:
        ...


def build_pipeline() -> list[PipelineStage]:
    """Create model stages in execution order.

    Import and append future models here. Each stage receives the graph returned
    by the preceding stage.
    """
    return [ModelTemplate.from_config()]


def run_pipeline(
    graph: CampusGraph,
    stages: Sequence[PipelineStage],
    debug_directory: str | Path | None = None,
) -> ModelOutput:
    """Run every stage and return the final output."""
    if not stages:
        raise ValueError("The pipeline must contain at least one model stage")

    model_input = ModelInput(graph=graph)
    final_output: ModelOutput | None = None

    for stage_number, stage in enumerate(stages, start=1):
        final_output = stage.predict(model_input)
        if debug_directory is not None:
            safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", final_output.model_name)
            debug_path = Path(debug_directory) / f"{stage_number:02d}_{safe_name}.json"
            final_output.save_json(debug_path)
        model_input = final_output.to_model_input()

    assert final_output is not None
    return final_output


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help="Directory containing nodes.csv and edges.csv",
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        help="Save the complete output from every stage as JSON",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    graph = load_campus_graph(arguments.dataset)
    final_output = run_pipeline(
        graph=graph,
        stages=build_pipeline(),
        debug_directory=arguments.debug_dir,
    )

    print(f"Pipeline completed: {final_output.model_name}")
    print(
        f"Generated '{final_output.property_name}' for "
        f"{len(final_output.property_values)} {final_output.property_target}s."
    )
    if arguments.debug_dir is not None:
        print(f"Debug outputs saved to: {arguments.debug_dir}")


if __name__ == "__main__":
    main()


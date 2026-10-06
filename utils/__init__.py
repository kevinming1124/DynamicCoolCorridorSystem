"""Shared graph and pipeline interfaces for the cool corridor system."""

from .graph import CampusGraph, GraphEdge, GraphNode, load_campus_graph
from .pipeline import ModelInput, ModelOutput

__all__ = [
    "CampusGraph",
    "GraphEdge",
    "GraphNode",
    "ModelInput",
    "ModelOutput",
    "load_campus_graph",
]


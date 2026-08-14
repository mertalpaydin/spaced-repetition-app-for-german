"""Taxonomy package for German Grammar Trainer."""

from src.taxonomy.facets import derive_facet, facet_space
from src.taxonomy.loader import load_taxonomy
from src.taxonomy.validator import TaxonomyValidator

__all__ = ["load_taxonomy", "TaxonomyValidator", "facet_space", "derive_facet"]

"""Taxonomy DAG validator and graph analysis module."""

from collections import defaultdict
from dataclasses import dataclass, field

from src.contracts import Topic


@dataclass
class ValidationReport:
    """Report detailing the results of taxonomy validation."""

    is_valid: bool
    topic_count: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cycles: list[list[str]] = field(default_factory=list)
    confusion_groups: dict[str, list[str]] = field(default_factory=dict)
    cefr_counts: dict[str, int] = field(default_factory=dict)


class TaxonomyValidator:
    """Validates the taxonomy DAG, prerequisite structure, and topic metadata."""

    def __init__(self, topics: list[Topic]) -> None:
        self.topics = topics
        self.topic_map: dict[str, Topic] = {t.id: t for t in topics}

    def validate(self) -> ValidationReport:
        """Run all validation checks against the topic list.

        Returns:
            A ValidationReport with details on errors, warnings, and DAG properties.
        """
        errors: list[str] = []
        warnings: list[str] = []

        # 1. Uniqueness of topic IDs
        seen_ids: set[str] = set()
        for t in self.topics:
            if t.id in seen_ids:
                errors.append(f"Duplicate topic ID: '{t.id}'")
            seen_ids.add(t.id)

        # 2. Referential integrity: all prereqs must exist in topic_map
        for t in self.topics:
            for p_id in t.prereqs:
                if p_id not in self.topic_map:
                    errors.append(f"Topic '{t.id}' references non-existent prerequisite '{p_id}'")

        # 3. Acyclicity: DAG must contain no directed cycles
        cycles = self._detect_cycles()
        if cycles:
            for c in cycles:
                errors.append(f"Cycle detected in prerequisite graph: {' -> '.join(c)}")

        # 4. Context requirements & eligibility checks
        for t in self.topics:
            if t.requires_context and "paragraph_cloze" not in t.eligible_types:
                errors.append(
                    f"Topic '{t.id}' requires context but does not have 'paragraph_cloze' "
                    f"in eligible_types: {t.eligible_types}"
                )
            if not t.eligible_types:
                errors.append(f"Topic '{t.id}' has empty eligible_types.")

        # 5. IntroCard validation
        for t in self.topics:
            if t.intro_card is None:
                warnings.append(f"Topic '{t.id}' is missing an intro_card.")
            elif isinstance(t.intro_card, str):
                if not t.intro_card.strip():
                    errors.append(f"Topic '{t.id}' intro_card string is empty.")
            else:
                if not t.intro_card.rule_de.strip():
                    errors.append(f"Topic '{t.id}' intro_card has empty rule_de.")
                if len(t.intro_card.worked_examples) < 1:
                    errors.append(f"Topic '{t.id}' intro_card must have at least 1 worked example.")

        # 6. Confusion group aggregation
        confusion_groups: dict[str, list[str]] = defaultdict(list)
        for t in self.topics:
            if t.confusion_group:
                confusion_groups[t.confusion_group].append(t.id)

        for cg_name, members in confusion_groups.items():
            if len(members) < 2:
                errors.append(f"Confusion group '{cg_name}' has fewer than 2 members: {members}")

        # 7. CEFR distribution
        cefr_counts: dict[str, int] = defaultdict(int)
        for t in self.topics:
            cefr_counts[t.cefr] += 1

        is_valid = len(errors) == 0
        return ValidationReport(
            is_valid=is_valid,
            topic_count=len(self.topics),
            errors=errors,
            warnings=warnings,
            cycles=cycles,
            confusion_groups=dict(confusion_groups),
            cefr_counts=dict(cefr_counts),
        )

    def _detect_cycles(self) -> list[list[str]]:
        """Detect directed cycles in the prerequisite DAG using DFS."""
        adj: dict[str, list[str]] = {t.id: list(t.prereqs) for t in self.topics}
        visited: dict[str, int] = {}  # 0: unvisited, 1: visiting, 2: visited
        cycles: list[list[str]] = []

        def dfs(node: str, path: list[str]) -> None:
            visited[node] = 1
            for neighbor in adj.get(node, []):
                if neighbor not in self.topic_map:
                    continue
                if visited.get(neighbor, 0) == 1:
                    # Found cycle
                    cycle_start_idx = path.index(neighbor)
                    cycle = path[cycle_start_idx:] + [neighbor]
                    cycles.append(cycle)
                elif visited.get(neighbor, 0) == 0:
                    dfs(neighbor, path + [neighbor])
            visited[node] = 2

        for t in self.topics:
            if visited.get(t.id, 0) == 0:
                dfs(t.id, [t.id])

        return cycles

    def get_transitive_prereqs(self, topic_id: str) -> set[str]:
        """Compute all transitive prerequisites for a given topic ID."""
        if topic_id not in self.topic_map:
            raise KeyError(f"Topic ID '{topic_id}' not found in taxonomy.")

        visited: set[str] = set()
        stack: list[str] = list(self.topic_map[topic_id].prereqs)

        while stack:
            curr = stack.pop()
            if curr not in visited:
                visited.add(curr)
                if curr in self.topic_map:
                    stack.extend(self.topic_map[curr].prereqs)

        return visited

    def get_descendants(self, topic_id: str) -> set[str]:
        """Compute all direct and indirect downstream dependents of a topic."""
        # Build reverse adjacency map (prereq -> dependents)
        reverse_adj: dict[str, list[str]] = defaultdict(list)
        for t in self.topics:
            for p in t.prereqs:
                reverse_adj[p].append(t.id)

        visited: set[str] = set()
        stack: list[str] = list(reverse_adj.get(topic_id, []))

        while stack:
            curr = stack.pop()
            if curr not in visited:
                visited.add(curr)
                stack.extend(reverse_adj.get(curr, []))

        return visited

"""Caches that let the build rerun without rereading the parse.

The parse stage writes about 7.5M occurrences (3 GB, ``occurrences.jsonl``)
and runs once per corpus change. The mine and cards stages used to read,
validate and canonicalise every one of them on every run, about 15 minutes
each, so a fix that touched one line of ``requested.yaml`` cost an hour of
build before anything was translated or reviewed (measured 2026-10-28).
What that pass produces depends only on the file and on the code that
canonicalises, so it is kept, keyed on both:

* the miner's tally of the parse file (``UnitStats`` per unit), which the
  small occurrence files of the targeted stages are folded into;
* the canonical occurrences, each line tagged with the unit ids it can
  belong to, so the cards stage parses only the lines its units can use;

and, separately, the carrier validator's verdict on each sentence, keyed on
the validator's code and model.

A key that does not match is a miss and the slow pass runs as before, so a
stale cache costs time, never a different deck. The files live in the
gitignored build directory and are written by this program alone; the
tally is a pickle for that reason, never for anything shared.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import pickle
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from src.atomic_write import write_text_atomic
from src.phrases.occurrences import Occurrence
from src.phrases.units import UnitStats, aggregate_canonical, canonical_occurrence

#: Bumped when the layout of the cache files changes.
FORMAT_VERSION = 1

Stats = dict[tuple[str, str], UnitStats]


def _module_path(name: str) -> Path | None:
    try:
        spec = importlib.util.find_spec(name)
    except ImportError:  # "src.contracts.Case": a name, not a module
        return None
    if spec is None or spec.origin is None or not spec.origin.endswith(".py"):
        return None
    return Path(spec.origin)


def _src_imports(tree: ast.Module, package: str = "src", used: set[str] | None = None) -> set[str]:
    """The ``package`` modules a module imports, resolved to module names.
    With ``used``, only the imports whose local name is in it."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                if alias.name.split(".")[0] == package and (used is None or local in used):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if node.module.split(".")[0] != package:
                continue
            for alias in node.names:
                if used is not None and (alias.asname or alias.name) not in used:
                    continue
                # "from src.phrases import paradigms" imports a module, not a name
                child = f"{node.module}.{alias.name}"
                found.add(child if _module_path(child) is not None else node.module)
    return found


class _UsedNames(ast.NodeVisitor):
    """The names a module's code reads, imports and type annotations
    aside: an annotation changes no value the cache holds, and
    ``LemmaCounts`` in one was enough to tie the cache to every detector."""

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        pass

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        pass

    def visit_arg(self, node: ast.arg) -> None:
        pass  # the argument's annotation only

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for child in [
            *node.decorator_list,
            *node.args.defaults,
            *node.args.kw_defaults,
            *node.body,
        ]:
            if child is not None:
                self.visit(child)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)


def source_fingerprint(
    root: str, *, skip_classes: frozenset[str] = frozenset(), package: str = "src"
) -> str:
    """A digest of ``root`` and the ``package`` modules its code depends on,
    transitively.

    Taken over the syntax tree, so a comment or a reflowed line changes
    nothing. ``skip_classes`` leaves top-level classes of ``root`` out: the
    miner's decisions live in ``UnitBuilder`` and change weekly, while the
    canonicalisation the cache holds does not depend on them. For ``root``,
    only the imports the remaining code reads are followed (2026-11-04):
    following all of them tied the cache to the curated lists and every
    detector, which only ``UnitBuilder`` uses, so a fix to a list or a
    detector cost a 14-minute rebuild for nothing. Every other module is
    followed whole, which errs towards rebuilding.
    """
    if _module_path(root) is None:
        # Never a digest of nothing: it would match every other nothing.
        raise ValueError(f"no Python source for module {root!r}")
    digest = hashlib.sha256()
    pending, seen = [root], set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        path = _module_path(name)
        if path is None:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        used: set[str] | None = None
        if name == root and skip_classes:
            tree.body = [
                node
                for node in tree.body
                if not (isinstance(node, ast.ClassDef) and node.name in skip_classes)
            ]
            reader = _UsedNames()
            reader.visit(tree)
            used = reader.names
            # The imports themselves stay out of the digest: an import only
            # the skipped class uses is not this code's business.
            tree.body = [n for n in tree.body if not isinstance(n, ast.Import | ast.ImportFrom)]
        digest.update(name.encode())
        digest.update(ast.dump(tree).encode())
        pending.extend(
            sorted(_src_imports(tree if used is None else _imports_of(path), package, used) - seen)
        )
    return digest.hexdigest()


def _imports_of(path: Path) -> ast.Module:
    """The module again, for its import statements (stripped from the
    digested tree above)."""
    return ast.parse(path.read_text(encoding="utf-8"))


def file_digest(path: Path) -> str:
    if not path.exists():
        return "missing"
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class ParseCacheKey(BaseModel):
    """What the cached pass over the parse file was computed from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: int = FORMAT_VERSION
    parse_size: int
    parse_mtime_ns: int
    code: str
    data: dict[str, str]


def parse_cache_key(parse_path: Path, data_paths: Iterable[Path]) -> ParseCacheKey:
    stat = parse_path.stat()
    return ParseCacheKey(
        parse_size=stat.st_size,
        parse_mtime_ns=stat.st_mtime_ns,
        code=source_fingerprint("src.phrases.units", skip_classes=frozenset({"UnitBuilder"})),
        data={p.name: file_digest(p) for p in sorted(data_paths)},
    )


class ParseCache:
    """The tally and the tagged canonical occurrences of one parse file."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.key_path = directory / "parse_key.json"
        self.stats_path = directory / "parse_stats.pickle"
        self.canonical_path = directory / "parse_canonical.tsv"

    def is_current(self, key: ParseCacheKey) -> bool:
        if not (self.stats_path.exists() and self.canonical_path.exists()):
            return False
        try:
            stored = ParseCacheKey.model_validate_json(self.key_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return stored == key

    def rebuild(
        self,
        parse_path: Path,
        key: ParseCacheKey,
        dictionary: frozenset[str] | None,
        infinitives: Mapping[str, object] | None,
        tag: Callable[[Occurrence], list[str]],
    ) -> Stats:
        """The one slow pass: tally the parse file and write both caches.

        The key goes first and last: removed before anything is replaced, so
        a run killed halfway can never leave an old key over new files, and
        written only once both files are in place.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        self.key_path.unlink(missing_ok=True)
        canonical_tmp = self.canonical_path.with_suffix(".tmp")
        with canonical_tmp.open("w", encoding="utf-8", newline="\n") as out:

            def canonical() -> Iterator[Occurrence]:
                with parse_path.open(encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        occ = canonical_occurrence(
                            Occurrence.model_validate_json(line), dictionary, infinitives
                        )
                        if occ is None:
                            continue
                        out.write(" ".join(tag(occ)))
                        out.write("\t")
                        out.write(occ.model_dump_json())
                        out.write("\n")
                        yield occ

            stats = aggregate_canonical(canonical())
        stats_tmp = self.stats_path.with_suffix(".tmp")
        with stats_tmp.open("wb") as handle:
            pickle.dump(stats, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(canonical_tmp, self.canonical_path)
        os.replace(stats_tmp, self.stats_path)
        write_text_atomic(self.key_path, key.model_dump_json())
        return stats

    def load_stats(self) -> Stats:
        with self.stats_path.open("rb") as handle:
            stats: Stats = pickle.load(handle)  # noqa: S301 - written by rebuild, above
        return stats

    def canonical_for(self, wanted_ids: frozenset[str] | set[str]) -> Iterator[Occurrence]:
        """The canonical occurrences, in parse-file order, of the lines
        tagged with a wanted id. Only those are parsed."""
        with self.canonical_path.open(encoding="utf-8") as handle:
            for line in handle:
                ids, _, payload = line.partition("\t")
                if not wanted_ids.isdisjoint(ids.split(" ")):
                    yield Occurrence.model_validate_json(payload)


class VerdictStore(BaseModel):
    """The carrier validator's verdicts by sentence, and what made them."""

    model_config = ConfigDict(extra="forbid")

    key: str
    verdicts: dict[str, bool]


def verdict_key(model_version: str, data_paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    digest.update(source_fingerprint("src.phrases.carrier_validation").encode())
    digest.update(model_version.encode())
    for path in sorted(data_paths):
        digest.update(file_digest(path).encode())
    return digest.hexdigest()


class VerdictCache:
    """Carrier verdicts kept between runs. The validator parses each
    sentence with spaCy, 82k of them per cards run, and its answer for a
    sentence changes only when its code or model does."""

    def __init__(self, path: Path, key: str) -> None:
        self.path = path
        self.key = key
        self.verdicts: dict[str, bool] = {}
        self.added = 0
        try:
            stored = VerdictStore.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if stored.key == key:
            self.verdicts = stored.verdicts

    def wrap(self, validate: Callable[[str], bool]) -> Callable[[str], bool]:
        def cached(text: str) -> bool:
            verdict = self.verdicts.get(text)
            if verdict is None:
                verdict = validate(text)
                self.verdicts[text] = verdict
                self.added += 1
            return verdict

        return cached

    def save(self) -> None:
        if self.added:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            store = VerdictStore(key=self.key, verdicts=dict(sorted(self.verdicts.items())))
            write_text_atomic(self.path, store.model_dump_json())

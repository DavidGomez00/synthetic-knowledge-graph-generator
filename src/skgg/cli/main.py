"""End-to-end experiment entry point: metrics → EDB → IDB → synthetic graph.

See `run_synthetic_graph_experiment` and AGENTS.md's "Running an experiment"
section for the full pipeline description.
"""

import argparse
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from SPARQLWrapper import SPARQLWrapper

from skgg.config import RunConfig
from skgg.core.queries import get_predicate_frequencies, get_support, get_triple_count
from skgg.core.rules import (
    Atom,
    HornRule,
    get_relation_graph,
    parse_rule_set,
)
from skgg.core.visualization import plot_relation_graph
from skgg.engine.completion import complete_graph
from skgg.engine.cycles import break_cycles
from skgg.engine.edb import generate_extensional_predicates
from skgg.engine.generator import get_closed_preds, get_closed_rules
from skgg.engine.metrics import GraphMetrics, PredicateProfile
from skgg.utils import (
    build_term_mapping,
    create_sparql_client,
    resolve_config_path,
    setup_logging,
    short_term,
)

logger = logging.getLogger(__name__)

_TOTAL_PHASES = 5


def _log_phase(number: int, title: str) -> None:
    """Logs a pipeline stage header, e.g. `[3/5] Completing graph`."""
    logger.info("[%d/%d] %s", number, _TOTAL_PHASES, title)


def _format_rule(rule: HornRule) -> str:
    """Formats a rule as `body atom & body atom => head` with shortened URIs."""

    def atom(a: Atom) -> str:
        return f"{short_term(a.subject)} {short_term(a.predicate)} {short_term(a.obj)}"

    body = " & ".join(atom(a) for a in sorted(rule.body))
    return f"{body} => {atom(rule.head)}"


@dataclass
class _ClosureProgress:
    """Logs the closure state between pipeline steps: how many predicates and
    rules are closed, which ones closed since the previous call, and each open
    predicate's current frequency against its target."""

    client: SPARQLWrapper
    profiles: dict[str, PredicateProfile]
    rules: dict[str, HornRule]
    # Target frequency per predicate, copied on creation: EDB generation later
    # spends `PredicateProfile.frequency` as a remaining budget.
    targets: dict[str, int] = field(init=False)
    closed_preds: set[str] = field(default_factory=set)
    closed_rules: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.targets = {p: pr.frequency for p, pr in self.profiles.items()}

    def log(self, stage: str, graph_uri: str) -> None:
        """Logs the closure state after `stage`, measuring open predicates'
        frequencies in `graph_uri`."""
        closed_preds = {p for p, pr in self.profiles.items() if pr.closed}
        closed_rules = {r_id for r_id, rule in self.rules.items() if rule.closed}
        new_preds = sorted(short_term(p) for p in closed_preds - self.closed_preds)
        new_rules = sorted(closed_rules - self.closed_rules)
        self.closed_preds, self.closed_rules = closed_preds, closed_rules

        logger.info(
            "[%s] Closed predicates: %d/%d, closed rules: %d/%d.",
            stage,
            len(closed_preds),
            len(self.profiles),
            len(closed_rules),
            len(self.rules),
        )
        if new_preds:
            logger.info("[%s] Newly closed predicates: %s", stage, ", ".join(new_preds))
        if new_rules:
            logger.info("[%s] Newly closed rules: %s", stage, ", ".join(new_rules))

        open_preds = sorted(self.profiles.keys() - closed_preds, key=short_term)
        if open_preds:
            # Profiles are keyed by the bracketed URI, frequencies by the bare one.
            freqs = get_predicate_frequencies(self.client, graph_uri)
            logger.info(
                "[%s] Open predicates (current/target): %s",
                stage,
                ", ".join(
                    f"{short_term(p)} {freqs.get(p[1:-1], 0)}/{self.targets[p]}"
                    for p in open_preds
                ),
            )
        if open_rules := sorted(self.rules.keys() - closed_rules):
            logger.debug("[%s] Open rules: %s", stage, ", ".join(open_rules))


def _format_summary_block(
    og_triple_count: int,
    syn_triple_count: int,
    og_freqs: dict[str, int],
    syn_freqs: dict[str, int],
    profiles: dict[str, PredicateProfile],
    rules: dict[str, HornRule],
    og_supports: dict[str, int],
    syn_supports: dict[str, int],
) -> str:
    """Formats one aligned report of the whole experiment: total triples,
    then one row per predicate and one row per rule showing original ->
    synthetic (delta) and open/closed status (read directly off
    PredicateProfile.closed / HornRule.closed, the authoritative closure
    state maintained throughout generation -- not re-derived here)."""
    triple_delta = syn_triple_count - og_triple_count
    pct = f", {triple_delta / og_triple_count:+.1%}" if og_triple_count else ""
    lines = [
        f"Triples: {og_triple_count} -> {syn_triple_count} ({triple_delta:+d}{pct})",
        "",
    ]

    known_preds = sorted(og_freqs)
    extra_preds = sorted(set(syn_freqs) - set(og_freqs))

    name_width = max((len(short_term(p)) for p in known_preds), default=0)
    og_width = max((len(str(og_freqs[p])) for p in known_preds), default=1)
    syn_width = max((len(str(syn_freqs.get(p, 0))) for p in known_preds), default=1)
    delta_width = max(
        (len(f"{syn_freqs.get(p, 0) - og_freqs[p]:+d}") for p in known_preds),
        default=1,
    )

    # A predicate is intensional iff some rule derives it (appears as a head).
    intensional_preds = {r.head.predicate for r in rules.values()}

    lines.append(f"Predicates ({len(known_preds)}):")
    for pred in known_preds:
        og, syn = og_freqs[pred], syn_freqs.get(pred, 0)
        kind = "INTENSIONAL" if f"<{pred}>" in intensional_preds else "EXTENSIONAL"
        # Profiles are keyed by the bracketed URI, frequencies by the bare one.
        status = "CLOSED" if profiles[f"<{pred}>"].closed else "OPEN"
        lines.append(
            f"  {short_term(pred).ljust(name_width)}  "
            f"{og:>{og_width}} -> {syn:<{syn_width}}"
            f"  ({syn - og:+{delta_width}d})"
            f"  {f'[{status}]':<8}  [{kind}]"
        )

    if extra_preds:
        extra_width = max(len(short_term(p)) for p in extra_preds)
        lines += [
            "",
            f"Predicates in synthetic but not original ({len(extra_preds)}):",
            *(
                f"  {short_term(pred).ljust(extra_width)}  {syn_freqs[pred]}"
                for pred in extra_preds
            ),
        ]

    rule_ids = sorted(rules)
    rule_texts = {rid: _format_rule(rules[rid]) for rid in rule_ids}
    rule_id_width = max((len(rid) for rid in rule_ids), default=0)
    rule_width = max((len(r) for r in rule_texts.values()), default=0)
    og_sup_width = max((len(str(og_supports[rid])) for rid in rule_ids), default=1)
    syn_sup_width = max((len(str(syn_supports[rid])) for rid in rule_ids), default=1)
    sup_delta_width = max(
        (len(f"{syn_supports[rid] - og_supports[rid]:+d}") for rid in rule_ids),
        default=1,
    )

    lines += ["", f"Rules ({len(rule_ids)}):"]
    for rid in rule_ids:
        og_sup, syn_sup = og_supports[rid], syn_supports[rid]
        status = "CLOSED" if rules[rid].closed else "OPEN"
        lines.append(
            f"  {rid.ljust(rule_id_width)}  {rule_texts[rid].ljust(rule_width)}  "
            f"{og_sup:>{og_sup_width}} -> {syn_sup:<{syn_sup_width}}"
            f"  ({syn_sup - og_sup:+{sup_delta_width}d})  [{status}]"
        )

    return "\n".join(lines)


def log_summary(
    client: SPARQLWrapper,
    original_uri: str,
    synthetic_uri: str,
    rules: dict[str, HornRule],
    profiles: dict[str, PredicateProfile],
) -> None:
    """Logs one consolidated report comparing the synthetic graph against the
    original: total triples, per-predicate frequency, and per-rule support,
    each as original -> synthetic (delta) plus open/closed status. Replaces
    the previous summarize_progress()/summary() pair, which queried
    overlapping data twice and logged two separate, overlapping reports.
    """
    og_triple_count = get_triple_count(client, original_uri)
    syn_triple_count = get_triple_count(client, synthetic_uri)
    og_freqs = get_predicate_frequencies(client, original_uri)
    syn_freqs = get_predicate_frequencies(client, synthetic_uri)
    og_supports = {
        rid: get_support(client, rule, original_uri) for rid, rule in rules.items()
    }
    syn_supports = {
        rid: get_support(client, rule, synthetic_uri) for rid, rule in rules.items()
    }

    logger.info(
        "Experiment summary:\n%s",
        _format_summary_block(
            og_triple_count,
            syn_triple_count,
            og_freqs,
            syn_freqs,
            profiles,
            rules,
            og_supports,
            syn_supports,
        ),
    )


def run_synthetic_graph_experiment(
    config_file: Path,
    skip_edb_generation: bool = False,
    log_level: int | str | None = None,
    pca_threshold: float | None = None,
) -> None:
    """Runs a Synthetic Graph generation experiment.

    If `skip_edb_generation` is True, EDB generation is skipped entirely and
    the IDB step reuses whatever triples already sit at `config.graph.edb_uri`
    in the database (e.g. from a previous run) instead of regenerating them.

    `log_level`, if given, overrides `config.logging.level` for this run.
    `pca_threshold`, if given, overrides `config.rules.pca_threshold` for
    this run's rule filtering only.
    """

    ## ------ Setup ------
    config = RunConfig.from_json(config_file)
    setup_logging(level=log_level if log_level is not None else config.logging.level)
    logger.info("Confifuration correctly initialized.")

    input_dir = config.data.input_dir
    rules_file = input_dir / config.rules.rules_file

    client = create_sparql_client(config)

    run_start = time.time()

    ## ------ Extraction of predicate profiles from original graph -------
    _log_phase(1, "Extracting metrics and rules")

    graph_metrics = GraphMetrics.from_uri(client, config.graph.base_uri)

    ## ------ Previous evaluation of rules ------
    term_mapping = build_term_mapping(
        term_namespaces=config.graph.term_namespaces,
        default_namespace=config.graph.namespace,
    )

    rules = parse_rule_set(
        rules_file=rules_file,
        term_mapping=term_mapping,
        pca_threshold=(
            pca_threshold if pca_threshold is not None else config.rules.pca_threshold
        ),
    )

    plot_relation_graph(
        get_relation_graph(rules),
        Path("logs") / f"relation_graph_{config.graph.name}.png",
        title=f"{config.graph.name} — relation graph",
    )

    # Created before EDB generation, which spends the profiles' frequencies.
    progress = _ClosureProgress(client, graph_metrics.profiles, rules)

    ## ------ Initialization -------
    chunk_size = config.db_config.chunk_size
    edb_uri = config.graph.edb_uri
    synthetic_uri = config.graph.synthetic_uri

    ## ------ EDB Generation  ------
    _log_phase(2, "Generating EDB")
    start_time = time.time()

    if skip_edb_generation:
        logger.info(
            "Skipping EDB generation, reusing existing EDB at <%s> with %d triples",
            edb_uri,
            get_triple_count(client, edb_uri),
        )
        for predicate in get_closed_preds(client, edb_uri, graph_metrics.profiles):
            graph_metrics.profiles[predicate].closed = True

        for rule_id in get_closed_rules(client, edb_uri, rules):
            rules[rule_id].closed = True
    else:
        generate_extensional_predicates(
            client=client,
            term_mapping=term_mapping,
            rules=rules,
            edb_uri=edb_uri,
            chunk_size=chunk_size,
            profiles=graph_metrics.profiles,
        )

        extensional_preds_time = time.time() - start_time

        logger.info(
            "EDB generated in %.1fs at <%s> with %d triples.",
            extensional_preds_time,
            edb_uri,
            get_triple_count(client, edb_uri),
        )
    progress.log("EDB", edb_uri)

    _log_phase(3, "Completing graph")
    complete_graph(
        client=client,
        rules=rules,
        term_mapping=term_mapping,
        source=edb_uri,
        target_uri=synthetic_uri,
        chunk_size=chunk_size,
        profiles=graph_metrics.profiles,
        label="initial",
    )
    progress.log("initial", synthetic_uri)

    _log_phase(4, "Breaking rule cycles")
    round_no = 0
    seeded = 1
    while seeded:
        seeded = break_cycles(
            client=client,
            rules=rules,
            term_mapping=term_mapping,
            synthetic_uri=synthetic_uri,
            chunk_size=chunk_size,
            profiles=graph_metrics.profiles,
        )
        round_no += 1
        complete_graph(
            client=client,
            rules=rules,
            term_mapping=term_mapping,
            source=synthetic_uri,
            target_uri=synthetic_uri,
            chunk_size=chunk_size,
            profiles=graph_metrics.profiles,
            label=f"cycle round {round_no}",
        )
        progress.log(f"cycle round {round_no}", synthetic_uri)

    logger.info("No cycles to break or rules to complete.")

    _log_phase(5, "Summary")
    log_summary(
        client, config.graph.base_uri, synthetic_uri, rules, graph_metrics.profiles
    )

    logger.info("Execution finished after %.1fs.", time.time() - run_start)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Synthetic Knowledge Graph generation experiment."
    )
    parser.add_argument(
        "-f",
        "--config-file",
        required=True,
        help="Config file under configurations/ (e.g. french_royalty.source.json), "
        "or a path to one.",
    )
    parser.add_argument(
        "--skip-edb",
        action="store_true",
        help="Skip EDB generation and reuse the existing EDB graph.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Override the config file's logging level (e.g. DEBUG, INFO, WARNING).",
    )
    parser.add_argument(
        "--pca-threshold",
        type=float,
        default=None,
        help="Override the config file's rules.pca_threshold for this run only.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_synthetic_graph_experiment(
        resolve_config_path(args.config_file),
        skip_edb_generation=args.skip_edb,
        log_level=args.log_level,
        pca_threshold=args.pca_threshold,
    )

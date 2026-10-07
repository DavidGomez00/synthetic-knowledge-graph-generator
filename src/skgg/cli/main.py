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

from skgg.core.config import CYCLE_REMOVAL_STRATEGIES, RunConfig
from skgg.core.queries import (
    copy_graph,
    get_predicate_frequencies,
    get_support,
    get_triple_count,
)
from skgg.core.rules import (
    DEFAULT_STD_THRESHOLD,
    Atom,
    HornRule,
    get_impacted_rules,
    get_relation_graph,
    parse_rule_set,
    remove_cyclic_rules,
    remove_minimal_cyclic_rules,
    rule_sort_key,
    write_used_rules,
)
from skgg.core.utils import (
    build_term_mapping,
    create_sparql_client,
    resolve_config_path,
    setup_logging,
    short_term,
)
from skgg.core.visualization import plot_relation_graph
from skgg.engine.completion import complete_graph
from skgg.engine.edb import generate_extensional_predicates
from skgg.engine.fill import fill_open_predicates
from skgg.engine.generator import get_closed_preds, get_closed_rules
from skgg.engine.metrics import GraphMetrics, PredicateProfile

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
    extensional and intensional predicate's current frequency against its
    target."""

    client: SPARQLWrapper
    profiles: dict[str, PredicateProfile]
    rules: dict[str, HornRule]
    # Target frequency per predicate, copied on creation: EDB generation later
    # spends `PredicateProfile.frequency` as a remaining budget.
    targets: dict[str, int] = field(init=False)
    intensional_preds: set[str] = field(init=False)
    closed_preds: set[str] = field(default_factory=set)
    closed_rules: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.targets = {p: pr.frequency for p, pr in self.profiles.items()}
        self.intensional_preds = {r.head.predicate for r in self.rules.values()}

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
            intensional = [p for p in open_preds if p in self.intensional_preds]
            extensional = [p for p in open_preds if p not in self.intensional_preds]
            for kind, preds in (
                ("extensional", extensional),
                ("intensional", intensional),
            ):
                if preds:
                    logger.info(
                        "[%s] Open %s predicates (current/target): %s",
                        stage,
                        kind,
                        ", ".join(
                            f"{short_term(p)} {freqs.get(p[1:-1], 0)}/{self.targets[p]}"
                            for p in preds
                        ),
                    )
        if open_rules := sorted(self.rules.keys() - closed_rules):
            logger.debug("[%s] Open rules: %s", stage, ", ".join(open_rules))


def _fill_open_relations(
    client: SPARQLWrapper,
    rules: dict[str, HornRule],
    progress: _ClosureProgress,
    source_uri: str,
    graph_uri: str,
    term_mapping: dict[str, str],
    chunk_size: int,
) -> None:
    """Lists each open relation with the rules a new triple of it could impact,
    and fills the open relations with `fill_open_predicates`, which also raises
    the support of open rules toward their target. Logs a warning if the support
    of a rule closed before the fill changed, or an open rule passed its target,
    which the fill checks should prevent."""
    profiles = progress.profiles
    open_preds = {p for p, pr in profiles.items() if not pr.closed}
    if not open_preds:
        logger.info("No open relations to fill.")
        return

    freqs = get_predicate_frequencies(client, graph_uri)
    impacted = get_impacted_rules(open_preds, rules)
    logger.info("Open relations (%d), current/target:", len(open_preds))
    for pred in sorted(open_preds, key=short_term):
        head_of, body_of = impacted[pred]
        logger.info(
            "  %s %d/%d: head of rules %s; in the body of rules %s.",
            short_term(pred),
            freqs.get(pred[1:-1], 0),
            progress.targets[pred],
            ", ".join(head_of) or "none",
            ", ".join(body_of) or "none",
        )

    supports = {r_id: get_support(client, r, graph_uri) for r_id, r in rules.items()}
    closed_before = {r_id for r_id, r in rules.items() if r.closed}
    fill_open_predicates(
        client=client,
        rules=rules,
        open_preds=open_preds,
        targets=progress.targets,
        source_uri=source_uri,
        graph_uri=graph_uri,
        term_mapping=term_mapping,
        chunk_size=chunk_size,
    )

    freqs = get_predicate_frequencies(client, graph_uri)
    for pred in open_preds:
        if freqs.get(pred[1:-1], 0) >= progress.targets[pred]:
            profiles[pred].closed = True
    for r_id in sorted(rules, key=rule_sort_key):
        rule, support = rules[r_id], get_support(client, rules[r_id], graph_uri)
        if r_id in closed_before:
            if support != supports[r_id]:
                logger.warning(
                    "[Fill] Support of closed rule %s changed: %d -> %d.",
                    r_id,
                    supports[r_id],
                    support,
                )
        elif support > rule.support:
            logger.warning(
                "[Fill] Rule %s passed its target support: %d -> %d/%d.",
                r_id,
                supports[r_id],
                support,
                int(rule.support),
            )
        else:
            logger.info(
                "[Fill] Rule %s: support %d -> %d/%d.",
                r_id,
                supports[r_id],
                support,
                int(rule.support),
            )


def _format_summary_block(
    og_triple_count: int,
    syn_triple_count: int,
    og_freqs: dict[str, int],
    syn_freqs: dict[str, int],
    profiles: dict[str, PredicateProfile],
    rules: dict[str, HornRule],
    removed_rules: dict[str, HornRule],
    og_supports: dict[str, int],
    syn_supports: dict[str, int],
) -> str:
    """Formats one aligned report of the whole experiment: total triples,
    then one row per predicate and one row per rule showing original ->
    synthetic (delta) and open/closed status (read directly off
    PredicateProfile.closed / HornRule.closed, the authoritative closure
    state maintained throughout generation -- not re-derived here). Rules
    removed by `remove_cyclic_rules` follow in their own section, without a
    status."""
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

    # Kept and removed rules share column widths, so both sections line up.
    all_rules = rules | removed_rules
    rule_texts = {rid: _format_rule(rule) for rid, rule in all_rules.items()}
    rule_id_width = max((len(rid) for rid in all_rules), default=0)
    rule_width = max((len(r) for r in rule_texts.values()), default=0)
    og_sup_width = max((len(str(og_supports[rid])) for rid in all_rules), default=1)
    syn_sup_width = max((len(str(syn_supports[rid])) for rid in all_rules), default=1)
    sup_delta_width = max(
        (len(f"{syn_supports[rid] - og_supports[rid]:+d}") for rid in all_rules),
        default=1,
    )

    def rule_row(rid: str) -> str:
        """Formats a rule's id, text and support original -> synthetic (delta)."""
        og_sup, syn_sup = og_supports[rid], syn_supports[rid]
        return (
            f"  {rid.ljust(rule_id_width)}  {rule_texts[rid].ljust(rule_width)}  "
            f"{og_sup:>{og_sup_width}} -> {syn_sup:<{syn_sup_width}}"
            f"  ({syn_sup - og_sup:+{sup_delta_width}d})"
        )

    rule_ids = sorted(rules, key=rule_sort_key)
    lines += ["", f"Rules ({len(rule_ids)}):"]
    for rid in rule_ids:
        status = "CLOSED" if rules[rid].closed else "OPEN"
        lines.append(f"{rule_row(rid)}  [{status}]")

    if removed_rules:
        lines += [
            "",
            f"Removed cyclic rules ({len(removed_rules)}):",
            *(rule_row(rid) for rid in sorted(removed_rules, key=rule_sort_key)),
        ]

    return "\n".join(lines)


def log_summary(
    client: SPARQLWrapper,
    original_uri: str,
    synthetic_uri: str,
    rules: dict[str, HornRule],
    profiles: dict[str, PredicateProfile],
    removed_rules: dict[str, HornRule],
) -> None:
    """Logs one consolidated report comparing the synthetic graph against the
    original: total triples, per-predicate frequency, and per-rule support,
    each as original -> synthetic (delta) plus open/closed status, then the
    support of each rule in `removed_rules` (see `remove_cyclic_rules`). Replaces
    the previous summarize_progress()/summary() pair, which queried
    overlapping data twice and logged two separate, overlapping reports.
    """
    og_triple_count = get_triple_count(client, original_uri)
    syn_triple_count = get_triple_count(client, synthetic_uri)
    og_freqs = get_predicate_frequencies(client, original_uri)
    syn_freqs = get_predicate_frequencies(client, synthetic_uri)
    all_rules = rules | removed_rules
    og_supports = {
        rid: get_support(client, rule, original_uri) for rid, rule in all_rules.items()
    }
    syn_supports = {
        rid: get_support(client, rule, synthetic_uri) for rid, rule in all_rules.items()
    }

    logger.info(
        "Experiment summary, <%s> -> <%s>:\n%s",
        original_uri,
        synthetic_uri,
        _format_summary_block(
            og_triple_count,
            syn_triple_count,
            og_freqs,
            syn_freqs,
            profiles,
            rules,
            removed_rules,
            og_supports,
            syn_supports,
        ),
    )


def run_synthetic_graph_experiment(
    config_file: Path,
    skip_edb_generation: bool = False,
    log_level: int | str | None = None,
    pca_threshold: float | None = None,
    cycle_removal: str | None = None,
    fill: bool = False,
) -> None:
    """Runs a Synthetic Graph generation experiment.

    If `skip_edb_generation` is True, EDB generation is skipped entirely and
    the IDB step reuses whatever triples already sit at `config.graph.edb_uri`
    in the database (e.g. from a previous run) instead of regenerating them.

    `log_level`, if given, overrides `config.logging.level` for this run.
    `pca_threshold`, if given, overrides `config.rules.pca_threshold` for
    this run's rule filtering only. If neither is set, the rules with std
    confidence >= `DEFAULT_STD_THRESHOLD` (1) are kept instead.

    After the confidence filter, cyclic rules are removed so that the relation
    graph has no cycle and completion can derive every intensional predicate from
    the EDB. `cycle_removal`, if given, overrides `config.rules.cycle_removal`:
    `"minimal"` removes the fewest rules (`remove_minimal_cyclic_rules`), `"all"`
    every rule on a cycle (`remove_cyclic_rules`).

    `config.graph.synthetic_uri` always holds the EDB plus what completion derives.
    If `fill` is True, it is copied to `config.graph.filled_uri` and the relations
    still open are filled there (`_fill_open_relations`); the summary then compares
    the filled graph with the source instead of the synthetic one.
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

    if pca_threshold is None:
        pca_threshold = config.rules.pca_threshold
    if pca_threshold is not None:
        rules = parse_rule_set(
            rules_file=rules_file,
            term_mapping=term_mapping,
            pca_threshold=pca_threshold,
        )
    else:
        rules = parse_rule_set(
            rules_file=rules_file,
            term_mapping=term_mapping,
            std_threshold=DEFAULT_STD_THRESHOLD,
        )
    # Drawn before the cyclic rules are removed, so the PNG shows their cycles.
    plot_relation_graph(
        get_relation_graph(rules),
        Path("logs") / f"relation_graph_{config.graph.name}.png",
        title=f"{config.graph.name} — relation graph",
    )
    # Before EDB generation, so a removed rule's head predicate is extensional.
    if cycle_removal is None:
        cycle_removal = config.rules.cycle_removal
    if cycle_removal == "all":
        removed_rules = remove_cyclic_rules(rules)
    else:
        removed_rules = remove_minimal_cyclic_rules(rules)

    # A predicate is intensional iff a remaining rule derives it.
    intensional_preds = {r.head.predicate for r in rules.values()}
    targets = {p: pr.frequency for p, pr in graph_metrics.profiles.items()}
    for kind, preds in (
        ("Extensional", targets.keys() - intensional_preds),
        ("Intensional", intensional_preds),
    ):
        logger.info(
            "%s relations (%d, target frequency): %s",
            kind,
            len(preds),
            ", ".join(
                f"{short_term(p)} {targets.get(p, 0)}"
                for p in sorted(preds, key=short_term)
            )
            or "none",
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
        write_used_rules(rules_file, rules.keys())
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

    _log_phase(3, "Completing graph")
    progress.log("EDB", edb_uri)
    logger.info(
        "Applying %d rules (%d open): %s",
        len(rules),
        sum(1 for r in rules.values() if not r.closed),
        ", ".join(
            f"{rid} ({'closed' if rules[rid].closed else 'open'})"
            for rid in sorted(rules, key=rule_sort_key)
        )
        or "none",
    )
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

    _log_phase(4, "Filling open relations")
    final_uri = synthetic_uri
    if fill:
        final_uri = config.graph.filled_uri
        copy_graph(client, synthetic_uri, final_uri)
        logger.info(
            "Copied <%s> to <%s> (%d triples) to fill it.",
            synthetic_uri,
            final_uri,
            get_triple_count(client, final_uri),
        )
        _fill_open_relations(
            client,
            rules,
            progress,
            config.graph.base_uri,
            final_uri,
            term_mapping,
            chunk_size,
        )
        progress.log("fill", final_uri)
    else:
        logger.info(
            "Skipping the fill step (pass --fill to run it); synthetic graph at <%s>.",
            synthetic_uri,
        )

    _log_phase(5, "Summary")
    log_summary(
        client,
        config.graph.base_uri,
        final_uri,
        rules,
        graph_metrics.profiles,
        removed_rules,
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
        help="Config file under configurations/ (e.g. fr.no-literals), or a path "
        "to one. The .json extension is optional.",
    )
    parser.add_argument(
        "--skip-edb",
        action="store_true",
        help="Skip EDB generation and reuse the existing EDB graph.",
    )
    parser.add_argument(
        "--fill",
        action="store_true",
        help="Fill the relations still open after completion, in a copy of the "
        "synthetic graph at the config's graph.filled_uri.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        help="Override the config file's logging level (e.g. DEBUG, INFO, WARNING).",
    )
    parser.add_argument(
        "--pca-conf",
        type=float,
        default=None,
        help="Keep the rules with at least this PCA confidence, overriding the "
        "config file's rules.pca_threshold for this run only. Without either, the "
        "rules with std confidence 1 are kept.",
    )
    parser.add_argument(
        "--cycle-removal",
        choices=CYCLE_REMOVAL_STRATEGIES,
        default=None,
        help="How cyclic rules are removed, overriding the config file's "
        "rules.cycle_removal for this run only: 'minimal' (the default) removes the "
        "fewest rules that break every cycle, 'all' every rule on a cycle.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_synthetic_graph_experiment(
        resolve_config_path(args.config_file),
        skip_edb_generation=args.skip_edb,
        log_level=args.log_level,
        pca_threshold=args.pca_conf,
        cycle_removal=args.cycle_removal,
        fill=args.fill,
    )

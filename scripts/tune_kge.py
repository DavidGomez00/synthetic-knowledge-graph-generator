"""Searches the hyperparameters of the KGC-Benchmark models, then trains with them.

The protocol is described in docs/kge_tuning.md. In short: one Optuna study per model
and search dataset (SEARCHES) with PyKEEN's `hpo_pipeline`. Every dataset is its own
search dataset, except the YAGO3-10 family, which is searched once on YAGO3-10_1_1.
Every model trains with batch size BATCH_SIZE and gets the same search space
(SHARED_RANGES: embedding dimension, learning rate, negatives per positive, uniform or
Bernoulli corruption); the hyperparameters PyKEEN would also search for some models
(margin, scoring norm, dropouts, relation dimension) stay at the model's defaults.
Trials are checked by MRR on a validation sample every CHECK_FREQUENCY epochs, for at
most `--max-epochs` epochs, end through early stopping or Hyperband pruning, and are
scored by that MRR. Every dataset is then trained with its search dataset's best
hyperparameters, choosing its number of epochs by early stopping on its own validation
sample.

Phase `tune` writes `<hpo-output>/<search dataset>/<model>/` (`study.db`, `trials.tsv`)
and `<hpo-output>/progress.csv`. A finished study is written to the record
(`--hyperparameters`) and to the table generated from it (`--table`). Phase `train`
reads only that record and writes `<output>` in the layout of `train_kge.py`, plus
`<output>/<dataset>/hparams/<model>.json` with the configuration each model used.

Both phases can be stopped at any moment and resume when run again. A trial in
progress is lost and run again with the same parameters, and a final run resumes from
its last epoch.

Needs pykeen (which brings optuna), torch, pandas and matplotlib, as train_kge.py does.
"""

import argparse
import csv
import gc
import inspect
import io
import json
import logging
import sys
import warnings
import zlib
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

import optuna
import pykeen
import torch
from optuna.distributions import distribution_to_json
from optuna.exceptions import ExperimentalWarning
from optuna.storages import RDBStorage, RetryHeartbeatStaleTrialCallback
from optuna.trial import TrialState
from pykeen.constants import PYKEEN_CHECKPOINTS
from pykeen.hpo import hpo_pipeline
from pykeen.models import model_resolver
from pykeen.sampling import (
    BasicNegativeSampler,
    BernoulliNegativeSampler,
    NegativeSampler,
)
from pykeen.triples import TriplesFactory
from train_kge import (
    INTERRUPTED_STATUS,
    LOCK_FILE,
    MODELS,
    RESULTS_FILE,
    Settings,
    Splits,
    dataset_lock,
    find_datasets,
    initialize_statuses,
    load_splits,
    train_dataset,
    write_text_atomically,
)

logger = logging.getLogger("tune_kge")
# The retry of killed trials is marked experimental by Optuna.
warnings.filterwarnings("ignore", category=ExperimentalWarning)
LOG_FILE = Path("logs/tune_kge.log")

# Each search dataset and the datasets trained with the hyperparameters found on it.
# Every dataset is searched on its own; the YAGO3-10 family is searched once, on its
# smallest dataset, because a search on each would take months.
YAGO_FAMILY = (
    "YAGO3-10",
    "YAGO3-10_1_1",
    "YAGO3-10_1_N",
    "YAGO3-10_N_1",
    "YAGO3-10_N_M",
)
SEARCHES: dict[str, tuple[str, ...]] = {
    **{
        name: (name,)
        for name in (
            "family", "Family1_1", "Family1_N", "FamilyM_N", "FamilyN_1",
            "french", "french1_1", "french1_N", "frenchN_1", "frenchN_M",
            "WN18RR", "WN18RR1_1", "WN18RR1_N", "WN18RRN_1", "WN18RRN_M",
            "SGKG1", "SGKG2", "SGKG3", "SGKG4",
        )
    },
    "YAGO3-10_1_1": YAGO_FAMILY,
}  # fmt: skip
# Searches without CompGCN: one of its trials would take about a day on YAGO3-10_1_1.
NO_COMPGCN = frozenset({"YAGO3-10_1_1"})

TRIALS = 30
# Fewer trials where a single trial takes hours.
SEARCH_TRIALS = {"YAGO3-10_1_1": 10}
MAX_TRIAL_EPOCHS = 300
FINAL_EPOCHS = 300
# Epochs between two checks on the validation sample, in trials and final runs.
CHECK_FREQUENCY = 5
PATIENCE = 5
RELATIVE_DELTA = 0.01
# The checks and the score of a trial. Hits@1 would give TransE and TransD no signal:
# they rank the query entity first, so their Hits@1 stays near 0 whatever the
# hyperparameters.
CHECK_METRIC = "inverse_harmonic_mean_rank"
TRIAL_METRIC = CHECK_METRIC
REDUCTION_FACTOR = 3
VALIDATION_SAMPLE = 5000
SEED = 1235
# The best-weights files PyKEEN's early stopper writes to PYKEEN_CHECKPOINTS.
BEST_WEIGHTS_GLOB = "best-model-weights-*.pt"
# The best-weights file of a final run, next to its checkpoint.
BEST_WEIGHTS_SUFFIX = ".best.pt"
MAX_FAILED_TRIALS = 10
# The batch size of every trial and final run. At dimension 200, TuckER peaks at
# 6.1 GB with 512 and 32 negatives (11.7 GB with 64), and can't fit 1024 with 64
# negatives on a 24 GB GPU, where PyKEEN would split the batches into slow sub-batches.
BATCH_SIZE = 512
# The search space, the same for every model.
SHARED_RANGES: dict[str, dict[str, Any]] = {
    "model": {"embedding_dim": {"type": "categorical", "choices": [50, 100, 200]}},
    "optimizer": {"lr": {"type": "float", "low": 1e-4, "high": 1e-1, "log": True}},
    "negative_sampler": {
        # Over 32 cost time and memory and gave the worst trials of the calibration.
        "num_negs_per_pos": {"type": "int", "low": 1, "high": 32, "log": True},
        # Which side of a triple a negative replaces; see CorruptionSampler.
        "corruption": {"type": "categorical", "choices": ["uniform", "bernoulli"]},
    },
}
# The PyKEEN sampler of each `corruption` value, as the record and final runs name it.
CORRUPTION_SAMPLERS = {"uniform": "basic", "bernoulli": "bernoulli"}

STUDY_FILE = "study.db"
TRIALS_FILE = "trials.tsv"
HPARAMS_DIR = "hparams"
HPO_PROGRESS_FILE = "progress.csv"
RECORD_LOCK = ".record.lock"
HPO_PROGRESS_COLUMNS = (
    "dataset",
    "model",
    "target",
    "complete",
    "pruned",
    "failed",
    "running",
    "best_mrr",
    "best_trial",
    "updated_at",
)


def constructor_defaults(cls: type, names: Any) -> dict[str, Any]:
    """The default values of the constructor parameters of `cls` in `names`."""
    parameters = inspect.signature(cls.__init__).parameters  # type: ignore[misc]
    return {name: parameters[name].default for name in names}


def pinned_kwargs(model: str) -> dict[str, dict[str, Any]]:
    """The model and loss kwargs that PyKEEN would search, fixed to their defaults.

    PyKEEN's HPO searches every entry of the `hpo_default` of the model and its loss
    unless it is given a value: the margin of the margin ranking loss, TransE's and
    TransH's scoring norm, TransD's and TuckER's relation dimension, TuckER's dropouts,
    and the weight of a default regularizer (TransH's). Each gets the model's default,
    so only SHARED_RANGES is searched.
    """
    cls = model_resolver.lookup(model)
    loss = cls.loss_default
    pinned = {
        "model_kwargs": constructor_defaults(
            cls, (k for k in cls.hpo_default if k not in SHARED_RANGES["model"])
        ),
        "loss_kwargs": constructor_defaults(loss, getattr(loss, "hpo_default", {})),
    }
    regularizer = getattr(cls, "regularizer_default", None)
    if regularizer is not None:
        defaults = getattr(cls, "regularizer_default_kwargs", None) or {}
        pinned["regularizer_kwargs"] = {
            k: defaults.get(k, constructor_defaults(regularizer, [k])[k])
            for k in getattr(regularizer, "hpo_default", {})
        }
    return pinned


def stopper_kwargs() -> dict[str, Any]:
    return {
        "frequency": CHECK_FREQUENCY,
        "patience": PATIENCE,
        "relative_delta": RELATIVE_DELTA,
        "metric": CHECK_METRIC,
    }


class CorruptionSampler(NegativeSampler):
    """PyKEEN's uniform or Bernoulli negative sampler, chosen by `corruption`.

    PyKEEN's HPO takes one sampler class, so the choice between the two is a parameter
    of this one. `uniform` (BasicNegativeSampler) replaces the head or the tail with
    the same probability. `bernoulli` (BernoulliNegativeSampler, Wang et al. 2014)
    replaces the head of a relation's triples with probability tails-per-head /
    (tails-per-head + heads-per-tail), so a 1-N relation mostly gets its head
    replaced, where a random entity rarely makes a true triple. The best trial is
    recorded with the PyKEEN sampler itself (CORRUPTION_SAMPLERS), which the final runs
    use.
    """

    hpo_default: ClassVar[Mapping[str, Any]] = {}

    def __init__(
        self,
        *,
        mapped_triples: torch.Tensor,
        corruption: str = "uniform",
        **kwargs: Any,
    ) -> None:
        super().__init__(mapped_triples=mapped_triples, **kwargs)
        inner = {"uniform": BasicNegativeSampler, "bernoulli": BernoulliNegativeSampler}
        # Filtering is done by this sampler, so the inner one doesn't build a filterer.
        self._inner = inner[corruption](
            mapped_triples=mapped_triples,
            num_entities=self.num_entities,
            num_relations=self.num_relations,
            num_negs_per_pos=self.num_negs_per_pos,
        )

    def corrupt_batch(self, positive_batch: torch.Tensor) -> torch.Tensor:
        return self._inner.corrupt_batch(positive_batch=positive_batch)


def search_trials(dataset: str, trials: int | None) -> int:
    """The number of trials of a search: `--trials` if given, else its default."""
    return trials if trials is not None else SEARCH_TRIALS.get(dataset, TRIALS)


def protocol(max_epochs: int, trials: int) -> dict[str, Any]:
    """The settings of a search, stored with each of its results."""
    return {
        "trials": trials,
        "max_trial_epochs": max_epochs,
        "sampler": {"name": "TPESampler", "seed": study_seed_rule()},
        "pruner": {
            "name": "HyperbandPruner",
            "min_resource": CHECK_FREQUENCY,
            "max_resource": max_epochs,
            "reduction_factor": REDUCTION_FACTOR,
        },
        "stopper": stopper_kwargs(),
        "trial_metric": TRIAL_METRIC,
        "validation_sample": VALIDATION_SAMPLE,
        "validation_sample_seed": SEED,
        "training_loop": "sLCWA",
        "create_inverse_triples": True,
        "filtered_negative_sampling": True,
        "shared_ranges": SHARED_RANGES,
        "batch_size": BATCH_SIZE,
        "fixed_to_model_defaults": "pinned_kwargs()",
    }


def study_seed_rule() -> str:
    return f"{SEED} + crc32('<search dataset>/<model>') % 10**6 + trials so far"


def study_seed(dataset: str, model: str, trials_so_far: int) -> int:
    """The sampler seed of a study's next trial.

    A new sampler is made for every trial, so the seed changes with the trials already
    run. The study's name is part of it, so studies don't start from the same random
    configurations.
    """
    offset = zlib.crc32(f"{dataset}/{model}".encode()) % 10**6
    return SEED + offset + trials_so_far


def validation_sample(validation: TriplesFactory) -> TriplesFactory:
    """A fixed random sample of at most VALIDATION_SAMPLE validation triples."""
    if validation.num_triples <= VALIDATION_SAMPLE:
        return validation
    generator = torch.Generator().manual_seed(SEED)
    rows = torch.randperm(validation.num_triples, generator=generator)
    rows = rows[:VALIDATION_SAMPLE].sort().values
    return validation.clone_and_exchange_triples(validation.mapped_triples[rows])


def open_storage(path: Path) -> RDBStorage:
    """A study database whose trials, when killed, are failed and queued again."""
    return RDBStorage(
        f"sqlite:///{path.absolute()}",
        heartbeat_interval=60,
        grace_period=120,
        heartbeat_stale_trial_callback=RetryHeartbeatStaleTrialCallback(max_retry=1),
    )


def count_trials(study: optuna.Study) -> dict[str, int]:
    states = Counter(trial.state for trial in study.trials)
    return {
        "complete": states[TrialState.COMPLETE],
        "pruned": states[TrialState.PRUNED],
        "failed": states[TrialState.FAIL],
        "running": states[TrialState.RUNNING] + states[TrialState.WAITING],
    }


def run_trial(
    splits: Splits,
    sample: TriplesFactory,
    model: str,
    storage: RDBStorage,
    seed: int,
    max_epochs: int,
) -> None:
    """Runs one trial of a study with PyKEEN's hpo_pipeline.

    PyKEEN's early stopper saves the best weights of the trial to its checkpoint
    folder and deletes them only when it stops the trial itself, so a trial that is
    pruned or reaches max_epochs leaves them behind. They are deleted here: over a
    full search they would add up to several GB.
    """
    before = set(PYKEEN_CHECKPOINTS.glob(BEST_WEIGHTS_GLOB))
    try:
        _run_hpo_trial(splits, sample, model, storage, seed, max_epochs)
    finally:
        for leftover in set(PYKEEN_CHECKPOINTS.glob(BEST_WEIGHTS_GLOB)) - before:
            leftover.unlink(missing_ok=True)


def _run_hpo_trial(
    splits: Splits,
    sample: TriplesFactory,
    model: str,
    storage: RDBStorage,
    seed: int,
    max_epochs: int,
) -> None:
    training, validation, testing = splits
    pinned = pinned_kwargs(model)
    hpo_pipeline(
        training=training,
        validation=sample,
        testing=testing,
        model=model,
        model_kwargs=pinned["model_kwargs"],
        loss_kwargs=pinned["loss_kwargs"],
        regularizer_kwargs=pinned.get("regularizer_kwargs"),
        model_kwargs_ranges=SHARED_RANGES["model"],
        optimizer="Adam",
        optimizer_kwargs_ranges=SHARED_RANGES["optimizer"],
        negative_sampler=CorruptionSampler,
        negative_sampler_kwargs={"filtered": True},
        negative_sampler_kwargs_ranges=SHARED_RANGES["negative_sampler"],
        training_loop="sLCWA",
        # A fixed batch size also overrides PyKEEN's default batch size range.
        training_kwargs={
            "num_epochs": max_epochs,
            "batch_size": BATCH_SIZE,
            "use_tqdm_batch": False,
        },
        stopper="early",
        stopper_kwargs=stopper_kwargs(),
        # Known answers of the whole validation split, not only of the sample.
        evaluation_kwargs={"additional_filter_triples": [validation.mapped_triples]},
        metric=TRIAL_METRIC,
        direction="maximize",
        storage=storage,
        study_name=model,
        load_if_exists=True,
        sampler="tpe",
        sampler_kwargs={"seed": seed},
        pruner="hyperband",
        pruner_kwargs={
            "min_resource": CHECK_FREQUENCY,
            "max_resource": max_epochs,
            "reduction_factor": REDUCTION_FACTOR,
        },
        n_trials=1,
    )


def best_config(model: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """The pipeline configuration of a trial, as PyKEEN's best_pipeline builds it."""
    pinned = pinned_kwargs(model)
    config: dict[str, Any] = {
        "model": model,
        "model_kwargs": dict(pinned["model_kwargs"]),
        "loss": model_resolver.lookup(model).loss_default.__name__,
        "loss_kwargs": dict(pinned["loss_kwargs"]),
        **(
            {"regularizer_kwargs": dict(pinned["regularizer_kwargs"])}
            if "regularizer_kwargs" in pinned
            else {}
        ),
        "optimizer": "Adam",
        "negative_sampler": "basic",
        "negative_sampler_kwargs": {"filtered": True},
        "training_loop": "sLCWA",
        "training_kwargs": {"batch_size": BATCH_SIZE},
    }
    for key, value in params.items():
        component, name = key.split(".", 1)
        if key == "negative_sampler.corruption":
            config["negative_sampler"] = CORRUPTION_SAMPLERS[value]
            continue
        config.setdefault(f"{component}_kwargs", {})[name] = value
    return config


def tune_study(
    dataset: str,
    model: str,
    splits: Splits,
    sample: TriplesFactory,
    hpo_output: Path,
    trials: int,
    max_epochs: int,
) -> dict[str, Any]:
    """Runs a study until it has `trials` finished trials and returns its best one.

    Raises:
        RuntimeError: If MAX_FAILED_TRIALS trials failed in this run, or none
            completed.
    """
    folder = hpo_output / dataset / model
    folder.mkdir(parents=True, exist_ok=True)
    storage = open_storage(folder / STUDY_FILE)
    failed_before: int | None = None
    while True:
        study = optuna.create_study(
            storage=storage, study_name=model, direction="maximize", load_if_exists=True
        )
        study.set_user_attr("target", trials)
        counts = count_trials(study)
        done = counts["complete"] + counts["pruned"]
        if done >= trials:
            break
        # Each run may fail MAX_FAILED_TRIALS trials, so a later run tries again.
        if failed_before is None:
            failed_before = counts["failed"]
        if counts["failed"] - failed_before >= MAX_FAILED_TRIALS:
            raise RuntimeError(f"{MAX_FAILED_TRIALS} trials of the study failed.")
        logger.info("%s: %s trial %d of %d.", dataset, model, done + 1, trials)
        try:
            run_trial(
                splits,
                sample,
                model,
                storage,
                study_seed(dataset, model, len(study.trials)),
                max_epochs,
            )
        except Exception:
            # A trial running out of memory must not stop the study.
            logger.exception("%s: a %s trial failed.", dataset, model)
        finally:
            gc.collect()
            torch.cuda.empty_cache()
            write_trials(study, folder / TRIALS_FILE)
            write_hpo_progress(hpo_output)

    if counts["complete"] == 0:
        raise RuntimeError("No trial of the study completed.")
    best = study.best_trial
    return {
        "pipeline": best_config(model, best.params),
        "best_trial": best.number,
        "validation_mrr": best.value,
        "stopped_epoch": best.user_attrs.get("stopped_epoch"),
        "trial_random_seed": best.user_attrs.get("random_seed"),
        "trials": counts,
        "search_space": {
            name: json.loads(distribution_to_json(distribution))
            for name, distribution in best.distributions.items()
        },
        "protocol": protocol(max_epochs, trials),
        "versions": versions(),
        "searched_at": now(),
    }


def write_trials(study: optuna.Study, path: Path) -> None:
    table = study.trials_dataframe()
    write_text_atomically(path, table.to_csv(sep="\t", index=False))


def write_hpo_progress(hpo_output: Path) -> None:
    """Rewrites the progress table of the search from every study database."""
    rows = []
    for path in sorted(hpo_output.glob(f"*/*/{STUDY_FILE}")):
        model, dataset = path.parent.name, path.parent.parent.name
        study = optuna.load_study(study_name=model, storage=f"sqlite:///{path}")
        complete = [t for t in study.trials if t.state == TrialState.COMPLETE]
        best = max(complete, key=lambda t: t.value or 0.0) if complete else None
        ends = [t.datetime_complete for t in study.trials if t.datetime_complete]
        rows.append(
            {
                "dataset": dataset,
                "model": model,
                "target": study.user_attrs.get("target"),
                **count_trials(study),
                "best_mrr": best.value if best else None,
                "best_trial": best.number if best else None,
                "updated_at": max(ends).isoformat(timespec="seconds") if ends else None,
            }
        )
    table = io.StringIO()
    writer = csv.DictWriter(table, HPO_PROGRESS_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    write_text_atomically(hpo_output / HPO_PROGRESS_FILE, table.getvalue())


def versions() -> dict[str, str]:
    return {
        "pykeen": pykeen.get_version(),
        "torch": torch.__version__,
        "optuna": optuna.__version__,
    }


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_record(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"searches": SEARCHES, "results": {}}
    record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return record


def save_result(
    record_path: Path,
    table_path: Path,
    hpo_output: Path,
    dataset: str,
    model: str,
    entry: dict[str, Any],
) -> None:
    """Adds a study's result to the record and regenerates the table."""
    with dataset_lock(hpo_output / RECORD_LOCK):
        record = read_record(record_path)
        record.pop("families", None)
        record["searches"] = SEARCHES
        record["results"].setdefault(dataset, {})[model] = entry
        record_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomically(record_path, json.dumps(record, indent=2) + "\n")
        table_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomically(table_path, render_table(record, record_path, table_path))


def render_table(record: dict[str, Any], record_path: Path, table_path: Path) -> str:
    """The Markdown page of the record: its protocol, then one row per study."""
    entries = [
        (dataset, model, entry)
        for dataset, models in record["results"].items()
        for model, entry in sorted(models.items())
    ]
    protocols = {json.dumps(e["protocol"], sort_keys=True) for _, _, e in entries}
    try:
        source = record_path.resolve().relative_to(table_path.resolve().parent.parent)
    except ValueError:
        source = record_path
    lines = [
        "# Tuned hyperparameters of the KGC-Benchmark models",
        "",
        f"Generated by `scripts/tune_kge.py` from `{source}`, so don't edit it by "
        "hand. The search protocol is described in [kge_tuning.md](kge_tuning.md).",
        "",
        "## Protocol",
        "",
    ]
    if len(protocols) == 1:
        settings = json.loads(protocols.pop())
        lines += [
            f"- `{key}`: `{json.dumps(value)}`" for key, value in settings.items()
        ]
    else:
        lines.append(
            "The studies used different protocols; see each entry of the record."
        )
    lines += [
        "",
        "## Best hyperparameters",
        "",
        "MRR is the best trial's score on the validation sample. The trials are "
        "complete / pruned / failed. The stopped epoch is the one the trial's early "
        "stopping chose; final runs choose their own.",
        "",
        "| Search dataset | Model | Validation MRR | Trials | Stopped epoch | "
        "Embedding dim | Batch size | Negatives per positive | Learning rate | Other |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for dataset, model, entry in entries:
        config = entry["pipeline"]
        model_kwargs = dict(config["model_kwargs"])
        dim = model_kwargs.pop("embedding_dim", "")
        other = [f"{k}={short(v)}" for k, v in model_kwargs.items()]
        other += [f"{k}={short(v)}" for k, v in config.get("loss_kwargs", {}).items()]
        other.append(f"sampler={config['negative_sampler']}")
        lr = config.get("optimizer_kwargs", {}).get("lr")
        trials = entry["trials"]
        lines.append(
            f"| {dataset} | {model} | {entry['validation_mrr']:.4f} | "
            f"{trials['complete']} / {trials['pruned']} / {trials['failed']} | "
            f"{entry['stopped_epoch'] or ''} | {dim} | "
            f"{config['training_kwargs'].get('batch_size', '')} | "
            f"{config['negative_sampler_kwargs'].get('num_negs_per_pos', '')} | "
            f"{f'{lr:.4g}' if lr else ''} | {', '.join(other)} |"
        )
    return "\n".join(lines) + "\n"


def short(value: Any) -> str:
    return f"{value:.4g}" if isinstance(value, float) else str(value)


def search_models(search: str, models: list[str]) -> list[str]:
    return [m for m in models if not (m == "CompGCN" and search in NO_COMPGCN)]


def tune(args: argparse.Namespace) -> list[str]:
    """Runs the studies of the chosen searches and returns the ones that failed."""
    failed = []
    for folder in find_datasets(args.root, args.searches or list(SEARCHES)):
        search = folder.name
        lock_folder = args.hpo_output / search
        lock_folder.mkdir(parents=True, exist_ok=True)
        with dataset_lock(lock_folder / LOCK_FILE):
            logger.info("%s: loading the splits of %s.", search, folder)
            splits = load_splits(folder, Settings())
            sample = validation_sample(splits[1])
            logger.info(
                "%s: checks use %d of %d validation triples.",
                search,
                sample.num_triples,
                splits[1].num_triples,
            )
            for model in search_models(search, args.models):
                try:
                    entry = tune_study(
                        search,
                        model,
                        splits,
                        sample,
                        args.hpo_output,
                        search_trials(search, args.trials),
                        args.max_epochs,
                    )
                except Exception:
                    logger.exception("%s: the %s study failed.", search, model)
                    failed.append(f"{search}/{model} (study)")
                    continue
                save_result(
                    args.hyperparameters,
                    args.table,
                    args.hpo_output,
                    search,
                    model,
                    entry,
                )
                logger.info(
                    "%s: %s searched, best validation MRR %.4f (trial %d).",
                    search,
                    model,
                    entry["validation_mrr"],
                    entry["best_trial"],
                )
    return failed


def final_kwargs_factory(
    search: str,
    entries: Mapping[str, Any],
    dataset_output: Path,
    checkpoints: Path,
    final_epochs: int,
) -> Callable[[str, Splits], dict[str, Any]]:
    """Builds each model's `train_model` overrides from the best trial of its search.

    The early stopper keeps the best weights in `<checkpoints>/<model>.best.pt`, a
    fixed path, so a resumed run finds them; remove_best_weights deletes the file once
    the model is finished.
    """

    def final_kwargs(model: str, splits: Splits) -> dict[str, Any]:
        _, validation, _ = splits
        sample = validation_sample(validation)
        config = entries[model]["pipeline"]
        hparams = {
            "search_dataset": search,
            "pipeline": config,
            "final_epochs": final_epochs,
            "stopper": stopper_kwargs(),
            "validation_sample": sample.num_triples,
        }
        check_hparams(dataset_output / HPARAMS_DIR / f"{model}.json", hparams)
        return {
            "model_kwargs": config["model_kwargs"],
            "loss_kwargs": config.get("loss_kwargs"),
            "regularizer_kwargs": config.get("regularizer_kwargs"),
            "optimizer_kwargs": config.get("optimizer_kwargs"),
            "negative_sampler": config["negative_sampler"],
            "negative_sampler_kwargs": config["negative_sampler_kwargs"],
            "training_kwargs": {
                **config["training_kwargs"],
                "num_epochs": final_epochs,
            },
            "validation": sample,
            # Test answers are ranked among the known answers of the whole split.
            "evaluation_kwargs": {
                "additional_filter_triples": [validation.mapped_triples]
            },
            "stopper": "early",
            "stopper_kwargs": {
                **stopper_kwargs(),
                "best_model_path": checkpoints / f"{model}{BEST_WEIGHTS_SUFFIX}",
            },
        }

    return final_kwargs


def remove_best_weights(
    dataset_output: Path, checkpoints: Path, models: list[str]
) -> None:
    """Deletes the best-weights file of each finished model.

    The early stopper deletes it only when it stops a run itself, not when the run
    reaches its last epoch.
    """
    for model in models:
        if (dataset_output / model / RESULTS_FILE).is_file():
            (checkpoints / f"{model}{BEST_WEIGHTS_SUFFIX}").unlink(missing_ok=True)


def check_hparams(path: Path, hparams: dict[str, Any]) -> None:
    """Records a model's configuration, or checks it against the recorded one.

    Raises:
        ValueError: If the model was started with another configuration.
    """
    if path.is_file():
        recorded = json.loads(path.read_text(encoding="utf-8"))
        if any(recorded.get(k) != v for k, v in hparams.items()):
            raise ValueError(
                f"{path} records other hyperparameters. Use another --output to "
                "train with the new ones."
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomically(path, json.dumps(hparams, indent=2) + "\n")


def record_best_epochs(dataset_output: Path, models: list[str]) -> None:
    """Adds the epoch early stopping kept to the configuration of finished models."""
    for model in models:
        results_file = dataset_output / model / RESULTS_FILE
        path = dataset_output / HPARAMS_DIR / f"{model}.json"
        if not (results_file.is_file() and path.is_file()):
            continue
        results = json.loads(results_file.read_text(encoding="utf-8"))
        stopper = results.get("stopper", {})
        hparams = json.loads(path.read_text(encoding="utf-8"))
        hparams.update(
            best_epoch=stopper.get("best_epoch"),
            stopped_early=stopper.get("stopped"),
            epochs_trained=len(results["losses"]),
        )
        write_text_atomically(path, json.dumps(hparams, indent=2) + "\n")


def train(args: argparse.Namespace) -> list[str]:
    """Trains every dataset with the hyperparameters of its search; returns failures."""
    record = read_record(args.hyperparameters)
    settings = Settings(num_epochs=args.final_epochs)
    recorded_settings = {
        "random_seed": settings.random_seed,
        "create_inverse_triples": settings.create_inverse_triples,
        "filtered_negative_sampling": settings.filtered_negative_sampling,
        "final_epochs": args.final_epochs,
        "hyperparameters": "tuned, see hparams/",
    }
    failed: list[str] = []
    for search in args.searches or list(SEARCHES):
        entries = record["results"].get(search, {})
        models = [m for m in search_models(search, args.models) if m in entries]
        missing = sorted(set(search_models(search, args.models)) - set(models))
        if missing:
            logger.warning(
                "%s: no tuned hyperparameters for %s, not trained.",
                search,
                ", ".join(missing),
            )
        names = [
            d
            for d in SEARCHES[search]
            if (args.root / d).is_dir()
            and (args.datasets is None or d in args.datasets)
        ]
        if not models or not names:
            continue
        folders = find_datasets(args.root, names)
        initialize_statuses(args.output, folders, models, args.final_epochs)
        for folder in folders:
            dataset_output = args.output / folder.name
            try:
                failed += train_dataset(
                    folder,
                    dataset_output,
                    args.checkpoints / folder.name,
                    models,
                    settings,
                    pipeline_kwargs=final_kwargs_factory(
                        search,
                        entries,
                        dataset_output,
                        args.checkpoints / folder.name,
                        args.final_epochs,
                    ),
                    recorded_settings=recorded_settings,
                )
            except Exception:
                logger.exception("%s failed.", folder.name)
                failed.append(folder.name)
            record_best_epochs(dataset_output, models)
            remove_best_weights(dataset_output, args.checkpoints / folder.name, models)
    return failed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search the hyperparameters of KGC-Benchmark's models per dataset, "
        "then train every dataset with them. See docs/kge_tuning.md."
    )
    parser.add_argument(
        "--phase",
        choices=("tune", "train", "all"),
        default="all",
        help="search, train with the record, or both (default: %(default)s)",
    )
    parser.add_argument("--root", type=Path, default=Path("exp_datasets"))
    parser.add_argument(
        "--hpo-output",
        type=Path,
        default=Path("exp_hpo"),
        help="folder for the studies (default: %(default)s)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("exp_results_hpo"),
        help="folder for the final runs (default: %(default)s)",
    )
    parser.add_argument("--checkpoints", type=Path, default=Path("exp_checkpoints_hpo"))
    parser.add_argument(
        "--hyperparameters",
        type=Path,
        default=Path("configurations/kge_hyperparameters.json"),
        help="record of the best hyperparameters (default: %(default)s)",
    )
    parser.add_argument(
        "--table",
        type=Path,
        default=Path("docs/kge_hyperparameters.md"),
        help="Markdown table generated from the record (default: %(default)s)",
    )
    parser.add_argument(
        "--searches",
        nargs="+",
        choices=list(SEARCHES),
        help="search datasets to process (default: all)",
    )
    parser.add_argument(
        "--datasets", nargs="+", help="datasets to train in phase train (default: all)"
    )
    parser.add_argument("--models", nargs="+", default=list(MODELS))
    parser.add_argument("--skip-models", nargs="+", default=[])
    parser.add_argument(
        "--trials",
        type=int,
        help=f"trials per study (default: {TRIALS}, or SEARCH_TRIALS)",
    )
    parser.add_argument(
        "--max-epochs",
        type=int,
        default=MAX_TRIAL_EPOCHS,
        help="epochs of a trial at most (default: %(default)s)",
    )
    parser.add_argument(
        "--final-epochs",
        type=int,
        default=FINAL_EPOCHS,
        help="epochs of a final run at most (default: %(default)s)",
    )
    args = parser.parse_args()
    unknown = sorted(set(args.skip_models) - set(args.models))
    if unknown:
        parser.error(f"--skip-models has models not in --models: {', '.join(unknown)}")
    args.models = [m for m in args.models if m not in args.skip_models]
    return args


def main() -> int:
    args = parse_args()
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_FILE)],
    )
    logging.getLogger("pykeen.training.training_loop").setLevel(logging.WARNING)
    logger.info("Command: %s", " ".join(sys.argv))
    logger.info(
        "PyKEEN %s, PyTorch %s, Optuna %s, GPU: %s.",
        pykeen.get_version(),
        torch.__version__,
        optuna.__version__,
        torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none",
    )
    args.hpo_output.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    try:
        if args.phase in ("tune", "all"):
            failed += tune(args)
        if args.phase in ("train", "all"):
            failed += train(args)
    except KeyboardInterrupt:
        logger.info("Interrupted. Run the same command to resume.")
        return INTERRUPTED_STATUS
    if failed:
        logger.error("Failed, retried by the next run: %s", ", ".join(failed))
        return 1
    logger.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

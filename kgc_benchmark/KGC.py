import gc
import json
import logging
import os
import re

import numpy as np
import pandas as pd
import torch
from matplotlib import pyplot as plt
from pykeen import predict
from pykeen.hpo import hpo_pipeline
from pykeen.pipeline import pipeline, plot_losses
from pykeen.triples import TriplesFactory


def main():
    # Set default configuration file path
    config_file = "input.json"

    # Load configuration from JSON file
    try:
        with open(config_file, "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Error: Configuration file {config_file} not found!")
        return
    except json.JSONDecodeError:
        print(f"Error: Configuration file {config_file} contains invalid JSON!")
        return

    # Set up logging
    log_level = config.get("log_level", "INFO")
    logging.basicConfig(level=getattr(logging, log_level))
    logger = logging.getLogger(__name__)

    # Clear GPU memory
    torch.cuda.empty_cache()
    gc.collect()

    # Extract configuration parameters
    kg_path = config.get("kg_path")
    results_path = config.get("results_path")
    models = config.get(
        "models", ["TransE", "TransH", "TransD", "ComplEx", "RotatE", "TuckER"]
    )
    num_epochs = config.get("num_epochs", 100)
    embedding_dim = config.get("embedding_dim", 50)
    batch_size = config.get("batch_size", 1024)
    random_seed = config.get("random_seed", 1235)
    create_inverse_triples = config.get("create_inverse_triples", False)
    filtered_negative_sampling = config.get("filtered_negative_sampling", True)
    save_splits = config.get("save_splits", True)
    hpo = config.get("hpo", False)
    n_trials = config.get("n_trials", 30)
    validation_ratio = config.get("validation_ratio", 0.1)

    if not kg_path:
        logger.error("Knowledge graph path is required in the configuration file!")
        return

    if not results_path:
        logger.error("Results path is required in the configuration file!")
        return

    # Create results directory if it doesn't exist
    os.makedirs(results_path, exist_ok=True)

    try:
        # Convert an N-Triples graph to a .tsv next to it, then use the .tsv
        if kg_path.lower().endswith(".nt"):
            kg_path = convert_ntriples(kg_path)

        # Load dataset
        logger.info(f"Loading knowledge graph from {kg_path}")
        tf, triple_data, entity_label, relation_label = load_dataset(
            kg_path, create_inverse_triples
        )

        # Split into train and test
        training, testing = tf.split(random_state=random_seed)

        # Save splits if requested
        if save_splits:
            training_triples = pd.DataFrame(training.triples)
            training_triples.to_csv(
                os.path.join(results_path, "train"), index=False, sep="\t", header=False
            )
            logger.info(
                f"Saved training split to {os.path.join(results_path, 'train')}"
            )

            testing_triples = pd.DataFrame(testing.triples)
            testing_triples.to_csv(
                os.path.join(results_path, "test"), index=False, sep="\t", header=False
            )
            logger.info(f"Saved testing split to {os.path.join(results_path, 'test')}")

        # HPO scores its trials on a validation set held out of the training split,
        # so the test split stays the same as in a run without HPO
        if hpo:
            hpo_training, validation = training.split(
                [1 - validation_ratio], random_state=random_seed
            )

        # Train and evaluate models
        logger.info(f"Training the following models: {', '.join(models)}")
        for m in models:
            logger.info(f"Training {m} model")
            model_results_path = os.path.join(results_path, m)

            # Create model directory if it doesn't exist
            os.makedirs(model_results_path, exist_ok=True)

            if hpo:
                pipeline_kwargs = search_hyperparameters(
                    tf_training=hpo_training,
                    tf_validation=validation,
                    tf_testing=testing,
                    embedding=m,
                    n_epoch=num_epochs,
                    n_trials=n_trials,
                    path=os.path.join(model_results_path, "hpo"),
                    random_seed=random_seed,
                    filtered_negative_sampling=filtered_negative_sampling,
                )
            else:
                pipeline_kwargs = {
                    "model_kwargs": {"embedding_dim": embedding_dim},
                    "training_kwargs": {"batch_size": batch_size},
                }

            # Train model
            model, result = create_model(
                tf_training=training,
                tf_testing=testing,
                embedding=m,
                n_epoch=num_epochs,
                path=results_path,
                random_seed=random_seed,
                filtered_negative_sampling=filtered_negative_sampling,
                **pipeline_kwargs,
            )

            # Create loss plot
            plotting(result, m, results_path)

            logger.info(f"Finished training {m} model")

        logger.info("All models trained successfully!")

    except Exception as e:
        logger.error(f"Error during execution: {e!s}")
        import traceback

        logger.error(traceback.format_exc())


# One N-Triples statement: subject, predicate and object, then a final "."
NT_IRI = r"<[^>]*>"
NT_BLANK_NODE = r"_:\S+"
NT_LITERAL = r'"(?:[^"\\]|\\.)*"(?:@[A-Za-z0-9-]+|\^\^<[^>]*>)?'
NT_TRIPLE = re.compile(
    rf"\s*({NT_IRI}|{NT_BLANK_NODE})\s*({NT_IRI})\s*"
    rf"({NT_IRI}|{NT_BLANK_NODE}|{NT_LITERAL})\s*\.\s*(?:#.*)?"
)


def convert_ntriples(nt_path):
    """
    Convert an N-Triples file to a .tsv file with the same name, in the same folder.

    IRIs are written without their angle brackets; blank nodes and literals are
    written as they appear in the N-Triples file. A .tsv newer than the N-Triples
    file is reused instead of being converted again.

    Args:
        nt_path (str): Path to the .nt file

    Returns:
        str: Path to the .tsv file
    """
    logger = logging.getLogger(__name__)
    tsv_path = os.path.splitext(nt_path)[0] + ".tsv"
    if os.path.exists(tsv_path) and os.path.getmtime(tsv_path) >= os.path.getmtime(
        nt_path
    ):
        logger.info(f"Using {tsv_path}, already converted from {nt_path}")
        return tsv_path

    logger.info(f"Converting {nt_path} to {tsv_path}")
    triples = []
    with open(nt_path, encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = NT_TRIPLE.fullmatch(line)
            if not match:
                raise ValueError(
                    f"{nt_path}, line {line_number} is not an N-Triples statement: {line}"
                )
            terms = [
                term[1:-1] if term.startswith("<") else term for term in match.groups()
            ]
            if any("\t" in term for term in terms):
                raise ValueError(
                    f"{nt_path}, line {line_number} has a tab inside a term, "
                    f"which can't be written to a .tsv: {line}"
                )
            triples.append("\t".join(terms))

    # Write to a temporary file first, so an interrupted run leaves no partial .tsv
    with open(tsv_path + ".tmp", "w", encoding="utf-8") as f:
        f.write("\n".join(triples) + "\n")
    os.replace(tsv_path + ".tmp", tsv_path)
    logger.info(f"Wrote {len(triples)} triples to {tsv_path}")
    return tsv_path


def load_dataset(name, create_inverse_triples=False):
    """
    Load and preprocess the knowledge graph from a TSV file.

    Args:
        name (str): Path to the dataset file
        create_inverse_triples (bool): Whether to create inverse triples

    Returns:
        tuple: (TriplesFactory, raw_data, entity_labels, relation_labels)
    """
    logger = logging.getLogger(__name__)
    try:
        triple_data = open(name, encoding="utf-8").read().strip()
        data = np.array([triple.split("\t") for triple in triple_data.split("\n")])
        tf_data = TriplesFactory.from_labeled_triples(
            triples=data, create_inverse_triples=create_inverse_triples
        )
        entity_label = tf_data.entity_to_id.keys()
        relation_label = tf_data.relation_to_id.keys()

        logger.info(
            f"Loaded dataset with {len(entity_label)} entities and {len(relation_label)} relations"
        )
        return tf_data, triple_data, entity_label, relation_label

    except Exception as e:
        logger.error(f"Error loading dataset: {e!s}")
        raise


def create_model(
    tf_training,
    tf_testing,
    embedding,
    n_epoch,
    path,
    random_seed=1235,
    filtered_negative_sampling=True,
    model_kwargs=None,
    training_kwargs=None,
    negative_sampler_kwargs=None,
    **pipeline_kwargs,
):
    """
    Train KGE models with required hyperparameters

    Args:
        tf_training: Training triples factory
        tf_testing: Testing triples factory
        embedding: Model name
        n_epoch: Number of training epochs
        path: Path to save results
        random_seed: Random seed for reproducibility
        filtered_negative_sampling: Whether to use filtered negative sampling
        model_kwargs: Model hyperparameters, e.g. embedding_dim
        training_kwargs: Training hyperparameters besides the epochs, e.g. batch_size
        negative_sampler_kwargs: Negative sampler hyperparameters, e.g. num_negs_per_pos
        **pipeline_kwargs: Any other pipeline() kwargs, e.g. optimizer_kwargs found by HPO

    Returns:
        tuple: (trained_model, results)
    """
    logger = logging.getLogger(__name__)
    try:
        results = pipeline(
            training=tf_training,
            testing=tf_testing,
            model=embedding,
            training_loop="sLCWA",
            model_kwargs=model_kwargs,
            negative_sampler_kwargs={
                "filtered": filtered_negative_sampling,
                **(negative_sampler_kwargs or {}),
            },
            # Training configuration
            training_kwargs={
                "num_epochs": n_epoch,
                "use_tqdm_batch": True,
                **(training_kwargs or {}),
            },
            # Runtime configuration
            random_seed=random_seed,
            **pipeline_kwargs,
        )
        model = results.model
        results.save_to_directory(
            os.path.join(path, embedding)
        )  # save results to the directory
        logger.info(f"Model {embedding} trained and saved successfully")
        return model, results
    except Exception as e:
        logger.error(f"Error creating model {embedding}: {e!s}")
        raise


def get_model_specific_params(model):
    """
    Get model-specific hyperparameter ranges for HPO

    Args:
        model: Model name

    Returns:
        dict: pykeen model_kwargs_ranges for the model
    """
    # Common parameters for all models
    common_params = {
        "embedding_dim": {"type": "int", "low": 50, "high": 200, "q": 50},
    }

    # Model-specific parameters
    model_params = {
        "TransE": {
            **common_params,
            "scoring_fct_norm": {"type": "int", "low": 1, "high": 2},
        },
        "ComplEx": {
            **common_params,
            "regularizer": {"type": "categorical", "choices": [None, "LP"]},
        },
        "RotatE": {**common_params},
        "DistMult": {**common_params},
        "CompGCN": {**common_params},
        "ConvE": {
            **common_params,
            "input_channels": {"type": "int", "low": 1, "high": 3},
            "output_channels": {"type": "int", "low": 32, "high": 128, "q": 32},
            "kernel_height": {"type": "int", "low": 2, "high": 5},
            "kernel_width": {"type": "int", "low": 2, "high": 5},
            "embedding_height": {"type": "int", "low": 5, "high": 20, "q": 5},
            "embedding_width": {"type": "int", "low": 5, "high": 20, "q": 5},
            "input_dropout": {"type": "float", "low": 0.0, "high": 0.5},
            "feature_map_dropout": {"type": "float", "low": 0.0, "high": 0.5},
            "output_dropout": {"type": "float", "low": 0.0, "high": 0.5},
        },
        "TuckER": {
            **common_params,
            "relation_dim": {"type": "int", "low": 50, "high": 200, "q": 50},
            "dropout_0": {"type": "float", "low": 0.0, "high": 0.5},
            "dropout_1": {"type": "float", "low": 0.0, "high": 0.5},
            "dropout_2": {"type": "float", "low": 0.0, "high": 0.5},
            "apply_batch_normalization": {
                "type": "categorical",
                "choices": [True, False],
            },
        },
    }

    return model_params.get(model, common_params)


def search_hyperparameters(
    tf_training,
    tf_validation,
    tf_testing,
    embedding,
    n_epoch,
    n_trials,
    path,
    random_seed=1235,
    filtered_negative_sampling=True,
):
    """
    Search the hyperparameters of a KGE model with pykeen's HPO pipeline

    Each trial is trained on tf_training and scored by Hits@1 on tf_validation.

    Args:
        tf_training: Training triples factory, without the validation triples
        tf_validation: Validation triples factory
        tf_testing: Testing triples factory
        embedding: Model name
        n_epoch: Number of training epochs per trial
        n_trials: Number of trials
        path: Path to save the study
        random_seed: Random seed for reproducibility
        filtered_negative_sampling: Whether to use filtered negative sampling

    Returns:
        dict: The best trial's hyperparameters as pipeline() kwargs,
            e.g. {"model_kwargs": {"embedding_dim": 100}, "optimizer_kwargs": {"lr": 0.01}}
    """
    logger = logging.getLogger(__name__)
    logger.info(f"Searching hyperparameters for {embedding} over {n_trials} trials")
    results = hpo_pipeline(
        training=tf_training,
        testing=tf_testing,
        validation=tf_validation,
        model=embedding,
        training_loop="sLCWA",
        # Model hyperparameter ranges - model specific
        model_kwargs_ranges=get_model_specific_params(embedding),
        # Training hyperparameter ranges
        training_kwargs_ranges={
            "batch_size": {"type": "int", "low": 128, "high": 512, "q": 128},
        },
        negative_sampler_kwargs={"filtered": filtered_negative_sampling},
        negative_sampler_kwargs_ranges={
            "num_negs_per_pos": {"type": "int", "low": 1, "high": 10},
        },
        # Training configuration
        training_kwargs={"num_epochs": n_epoch, "use_tqdm": True},
        # HPO configuration
        n_trials=n_trials,
        metric="hits@1",
        direction="maximize",
        sampler_kwargs={"seed": random_seed},
    )
    results.save_to_directory(path)

    best_trial = results.study.best_trial
    logger.info(f"Best trial value for {embedding}: {best_trial.value}")
    logger.info("Best hyperparameters:")
    for key, value in best_trial.params.items():
        logger.info(f"{key}: {value}")

    # pykeen names each parameter "<component>.<name>", e.g. "optimizer.lr"
    pipeline_kwargs = {}
    for key, value in best_trial.params.items():
        component, name = key.split(".", 1)
        pipeline_kwargs.setdefault(f"{component}_kwargs", {})[name] = value
    return pipeline_kwargs


def plotting(result, m, results_path):
    """
    Plotting observed losses per KGE model

    Args:
        result: Pipeline result
        m: Model name
        results_path: Path to save plot
    """
    logger = logging.getLogger(__name__)
    try:
        plot_losses(result)
        plt.savefig(os.path.join(results_path, m, "loss_plot.png"), dpi=300)
        plt.close()  # Close the plot to free memory
        logger.info(f"Loss plot saved for model {m}")
    except Exception as e:
        logger.error(f"Error creating loss plot for model {m}: {e!s}")


def tail_prediction(model, head, relation, training):
    """
    Predict tail entity

    Args:
        model: Trained model
        head: Head entity
        relation: Relation entity
        training: Training triples factory

    Returns:
        DataFrame: Prediction results
    """
    pred = predict.predict_target(
        model=model, head=head, relation=relation, triples_factory=training
    ).df
    return pred


if __name__ == "__main__":
    main()

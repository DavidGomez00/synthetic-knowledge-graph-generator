# kbgen, as used for the rule-level copies

This folder is the code of kbgen (Melo and Paulheim, "Synthesizing Knowledge Graphs for Link and Type Prediction Benchmarking", ESWC 2017) that made the kbgen copies of `runs/rule-levels/`.

- Upstream: https://github.com/aolimelo/kbgen, commit `e5d06bc` ("Update model_m1.py"), the head of `master` on 16 November 2026. The repository has no license file.
- Changes: `baselines/kbgen.patch` of this repository (sha256 `cd9cca3c1870807c299d455ee845e3f227a384495515a97d47626a5b1624a3c9`) applied to that commit: Python 3 fixes in `kb_models/model_m1.py`, `model_m2.py`, `model_m3.py`, `load_tensor_tools.py`, `rules.py` and `util.py`, and the driver `synthesize_exp_datasets.py`, which is ours.
- After the patch, the driver was changed here to record the rule IDs given to kbgen (`given_rule_ids` in each level's `stats.json` and in `summary.csv`) and the time each level finished, and to default `--root` to this repository's `exp_datasets/`. `synthesize.py` also writes the model's counters to `<output>.counts.json` after synthesis, among them `count_rule_facts`, the facts added by a rule firing, which upstream only logs in debug mode; the driver copies it to `rule_facts` in `stats.json` and `summary.csv`. Neither change alters the synthesis. On 18 November 2026 the driver got `--entities source`, which passes `synthesize.py -ne` with the unique entities of the dataset's `.unified.tsv` (the copies in `exp_datasets_kbgen_entities/`, recorded in `runs/rule-levels-entities/`), and reads the generator's `_used.csv` files from `--root` whatever `--level-root` is, or from `--used-rules-dir` for rules selected without making the generator's copy (`runs/rule-levels-entities/select_rules.py`); with the default `--entities kept` the synthesis is unchanged. Every other file is identical to upstream plus the patch.
- `requirements.txt` is `pip freeze` of the environment that runs it (Python 3.10.22, `~/.pyenv/versions/KG` on Hefesto).

To rebuild this folder from upstream:

```bash
git clone https://github.com/aolimelo/kbgen && cd kbgen && git checkout e5d06bc
git apply /path/to/synthetic-knowledge-graph-generator/baselines/kbgen.patch
```

then copy `synthesize_exp_datasets.py` from this folder, which has the changes listed above.

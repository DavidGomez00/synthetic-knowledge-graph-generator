# Rule-level copies of the PCA setting

In the PCA setting a rule is complete when its PCA confidence is 1. It became the generator's only setting on 19 November 2026; until then the default was the std setting, where std confidence 1 made a rule complete (commit `2c7dfd5d`). This folder records the copies of the 17 levels where the two settings gave this generator different rules, and the remake of kbgen's family copies.

## Our copies (17 November 2026)

`generate.sh` made the 17 copies with `cli/generate_datasets.py --completeness pca`, the option of that time, into `exp_datasets_pca/`: family at pca1 and r25 to r100, and french, french1_1 and YAGO3-10_N_1 at r25 to r100. Its output is in `generate.out` and `steps.log`, and each copy's log in `ours/`.

On 19 November 2026 they were moved into `exp_datasets/`, replacing the std copies at the same levels. The std copies' splits, trained runs (`exp_results/`) and ranked predictions (`exp_analysis/`) were removed with them; commit `2c7dfd5d` has them. The moved copies were split again with `scripts/split_vanilla.py` and have to be trained again. `exp_datasets/rule_levels.tsv` was written again by `cli/rule_levels.py`, whose rows equal those of the former `exp_datasets_pca/rule_levels.tsv` without the columns `std_threshold` and `complete_changed`.

| Dataset | Rules of the PCA copy, against the std copy | Copy size, PCA against std |
|---|---|---|
| family | 26 complete rules against 18; pca1 is new (26 rules) | 4.3 to 4.9 times the source, against 3.2 to 3.6 |
| french | 4 fewer: 19, 181, 184, 201 | same |
| french1_1 | 5 fewer: 7, 145, 149, 161, 162 | same |
| YAGO3-10_N_1 | 1 fewer: 43 | same |

In french, french1_1 and YAGO3-10_N_1 these rules become complete in the PCA setting, close a cycle and are dropped by cycle removal. In family, 8 rules become complete, and 3 (43, 45, 50) are dropped by cycle removal.

## kbgen's copies at these levels

kbgen's copies at the 16 levels that existed were given the std copies' rules (`--rules-from-ours`): the PCA copy's rules plus those of the table. kbgen parsed all of them except 181 (french) and 145 (french1_1), which use `gender`, a relation its tensor drops.

- french, french1_1 and YAGO3-10_N_1 at r25 to r100 keep their kbgen copies, with 1 to 4 rules more than ours: french 19, 184, 201; french1_1 7, 149, 161, 162; YAGO3-10_N_1 43 (support 15 to 125).
- family's are remade from the PCA copies' rules, and `family_kbgen_pca1` is made. The extra rules there (43, 45, 50) have support 612, 595 and 1,346.

## Remaking kbgen's family copies

`remake_kbgen_family.sh` makes `family_kbgen_pca1` to `family_kbgen_r100` in `exp_datasets/`, replacing the copies there. It runs from a clone of the repository with no graph database:

1. Pull the branch with the PCA copies (`exp_datasets/family_ours_<level>/family_ours_<level>_used.csv`, 26 rules at pca1). The script stops if they are missing.
2. Install kbgen's packages (`runs/rule-levels/kbgen/requirements.txt`) and pass that interpreter as `PYTHON`.
3. Start it in tmux, so a lost connection doesn't stop it:

   ```bash
   tmux new-session -d -s kbgen-family 'PYTHON=~/.pyenv/versions/KG/bin/python bash runs/rule-levels-pca/remake_kbgen_family.sh 2>&1 | tee runs/rule-levels-pca/kbgen.out; exec bash'
   tmux attach -t kbgen-family      # Ctrl-b d to detach
   tail -f runs/rule-levels-pca/steps.log
   ```

4. The script writes `machine.txt`, builds kbgen's tensor and models in `kbgen-work/family/` (seconds), synthesizes the 5 levels at once, then checks that kbgen was given exactly our rules at each level and runs `scripts/check_kbgen_levels.py`. It splits the copies 80/20 if a VANILLA clone sits next to the repository.
5. Commit the 5 copies (with their `.log`, `.base.log`, `.stats.json`, `.rules.txt` and splits) and this folder (`.gitignore` keeps only the records of `kbgen-work/`), and push.
6. Then the old runs of `family_kbgen_r25` to `r100` in `exp_results/` and `exp_analysis/family/kbgen_<level>/` no longer match their copies: remove them and train the 5 copies with the 17 ours copies.

Each level is one single-threaded Python process, since kbgen checks the rules with rdflib queries for every fact it adds, so the wall time is that of the slowest level. On Hefesto (i9-7900X) family took 7.4 to 8.4 h per level at r50 to r100 and 0.5 h at r25; a CPU with faster cores shortens it, more cores don't.

## Training

```bash
python scripts/train_kge.py --datasets family_ours_pca1 family_ours_r25 family_ours_r50 family_ours_r75 family_ours_r100 \
    french_ours_r25 french_ours_r50 french_ours_r75 french_ours_r100 french1_1_ours_r25 french1_1_ours_r50 french1_1_ours_r75 french1_1_ours_r100 \
    YAGO3-10_N_1_ours_r25 YAGO3-10_N_1_ours_r50 YAGO3-10_N_1_ours_r75 YAGO3-10_N_1_ours_r100 \
    family_kbgen_pca1 family_kbgen_r25 family_kbgen_r50 family_kbgen_r75 family_kbgen_r100 --workers 12 --upload
```

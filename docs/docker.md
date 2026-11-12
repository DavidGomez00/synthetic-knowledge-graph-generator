# Running the experiments in Docker

The `Dockerfile` builds an image that trains the KGE benchmark on a machine with nothing but Docker and a GPU: no clone and no step after training. Started without arguments, the container copies the repository into its volume, updates it, downloads the runs other machines finished, trains with `scripts/train_kge.py --upload`, pushes each finished run to the `kge-results` branch and stays up idle. The other experiments (`cli/main.py`, rule mining) also run in it.

## What the image holds

- Ubuntu 24.04 and Python 3.10.22, built by [pyenv](https://github.com/pyenv/pyenv) in `/opt/pyenv`, the version `requirements.txt` was frozen from.
- The packages of `requirements.txt`, including PyTorch 2.14.1 from PyPI with its CUDA 13.0 libraries. The host needs an NVIDIA driver that supports CUDA 13.0 (580 or later) and the NVIDIA Container Toolkit.
- OpenJDK 21 (headless) and the AMIE3 jar at `/opt/amie/amie3.5.1.jar`, checked against its SHA-256. `AMIE_JAR` points at it, and `mine_rules/run_amie.py` uses that variable as its default `--jar`.
- A shallow clone of the repository in `/opt/skgg/repo`, datasets included (1.9 GB), owned by the image's user `skgg` (uid 1000). The container works in `/workspace`, a volume, and copies the clone there when it starts with an empty one.

The image is about 14 GB. The build uses no file from the host: BuildKit's `ADD` clones `REPO_URL` at `REPO_REF` (default `main` of the GitHub repository), so only commits pushed to GitHub are in it. BuildKit resolves the ref to a commit, so a build after a new commit takes the new code, and the Python packages (about 4 GB) are installed again only when `requirements.txt` changes.

## Building

On any machine with Docker, from the repository URL alone:

```bash
docker build -t skgg https://github.com/DavidGomez00/synthetic-knowledge-graph-generator.git#main
```

or from a clone, with `docker build -t skgg .` or `docker compose --profile skgg build skgg`. `--build-arg REPO_REF=<branch>` builds another branch.

## The token

Pushing to `kge-results` needs a GitHub fine-grained personal access token. Create it at GitHub → Settings → Developer settings → Fine-grained tokens with "Only select repositories" set to this repository and the repository permission "Contents: Read and write", nothing else. It can push to this repository only, and it expires on the date chosen when creating it (at most one year), after which uploads fail until a container gets a new one.

The container reads it from `GITHUB_TOKEN`. A git credential helper hands it to git when a push asks for it, so it is never written to the image or to disk. Without it the container trains as usual and logs a warning; each failed upload is logged and retried after the next run, and the last attempt is made when training ends. Runs that were never uploaded, e.g. because the token expired, stay in the volume. To push them with a new token:

```bash
docker run --rm -v skgg-data:/workspace -e GITHUB_TOKEN=<new token> -e SKGG_DOWNLOAD=0 skgg python scripts/upload_results.py
```

## Running

The command for the sysadmin of a training machine:

```bash
docker run -d --name skgg --gpus all --shm-size 8g --restart unless-stopped \
  -v skgg-data:/workspace --hostname <machine name> -e GITHUB_TOKEN=<token> \
  skgg --skip-models CompGCN --set ours --workers 4
```

- Arguments starting with `-` go to `python scripts/train_kge.py --upload`. Without arguments it runs `--skip-models CompGCN` on the base set. Any other command runs as given, e.g. `skgg python -m skgg.cli.main -f french_royalty.json` or `skgg bash`.
- `--hostname` names the machine in the commits of `kge-results` ("Add 3 runs trained on <machine name>"). Without it they carry the container ID.
- `-v skgg-data:/workspace` keeps the code, results, checkpoints and logs in the named volume `skgg-data`, which outlives the container. A folder of the host works too, e.g. `-v /srv/skgg:/workspace`, if it is empty and owned by uid 1000 (`sudo install -d -o 1000 -g 1000 /srv/skgg`). Without `-v`, Docker keeps `/workspace` in an anonymous volume, which a later container doesn't find by name.
- `--restart unless-stopped` starts the container again after a crash, a power cut or a reboot, as long as the Docker service starts at boot (`systemctl is-enabled docker`). Training resumes from the last finished epoch: checkpoints are written after every epoch with an atomic rename, so a cut loses at most the epoch in progress. Killing a container with `docker kill` and starting it again resumed at epoch 3 of 20 in testing.
- When training ends, the container stays up idle, so the restart policy doesn't start the training again and again. Its last log line says whether every run finished; `docker restart skgg` retries failed runs. `docker stop skgg` stops it, during training as well, and a stopped container is not restarted, not even after a reboot.
- `docker logs -f skgg` follows the training. `exp_results/progress.csv` in the volume has the progress, and `docker exec skgg python scripts/kge_eta.py --workers 4` the time left.

To run a new image, or other arguments, replace the container and keep the volume: training goes on from where it stopped.

```bash
docker rm -f skgg
docker run -d --name skgg ...   # the same command, with the same -v
```

`docker volume rm skgg-data` deletes everything the machine trained that is not on `kge-results`.

Machines should train different datasets (`--set`, `--datasets`, `--skip-datasets`): the locks of `train_kge.py` are local, so two machines given the same runs train them twice, and the branch keeps whichever is pushed first.

## What happens at start

`docker/entrypoint.sh`, installed in the image as `skgg-entrypoint`:

1. Copies `/opt/skgg/repo` into `/workspace` if `/workspace` has no `.git`. It refuses to start if `/workspace` holds other files or is not writable.
2. Points `origin` at `REPO_URL` and, if `GITHUB_TOKEN` is set, sets up the credential helper.
3. Fetches the latest commit of `REPO_REF` and checks it out, so the volume always runs the current code, whatever image wrote it. Results, checkpoints and logs are ignored by git and left alone. If the fetch fails, the code of the image is kept. `-e SKGG_UPDATE=0` skips this step. A change to `requirements.txt` still needs a new image.
4. Runs `scripts/upload_results.py --download`, which copies the runs already on `kge-results` into `exp_results/`, so `train_kge.py` skips them. A container with a new volume, on the same machine or another, therefore continues where the branch left off. The first time, when the branch doesn't exist yet, it logs an error and goes on. `-e SKGG_DOWNLOAD=0` skips this step, which saves downloading every finished model (about 1.7 GB per set of datasets).
5. Runs the command. After `train_kge.py` it stays up idle (`-e SKGG_IDLE=0` exits instead, with its status); `docker stop` is passed on to `train_kge.py` and the container exits. Any other command replaces the script, as with `exec`.

## The graph database

GraphDB is not in the image. `cli/main.py` and `cli/generate_datasets.py` need one, e.g. started with `docker compose --profile graphdb up -d`; with `--network host`, the configs' `http://localhost:7200/` reaches it. Training does not need it.

## Working on the code

To run your working copy instead of the volume, mount it over `/workspace`; it has a `.git`, so nothing is copied into it. Files the container writes belong to uid 1000:

```bash
docker run --rm -it --gpus all --shm-size 8g --network host -e SKGG_UPDATE=0 -e SKGG_DOWNLOAD=0 -v "$PWD":/workspace skgg bash
```

# FLAIR long-job spooler (R12)

The spooler is what lets coscientist run **long CPU/GPU jobs** on a FLAIR node
without giving its container root-equivalent power. It runs **on the node, as
your user, outside Docker**, and is the only thing that runs `docker`.

```
coscientist container ──writes job request──▶  state/jobspool/  ◀──watch── spooler (you, on the node)
        └─────────── polls status/logs ────────┘                          runs docker run --gpus …
```

Why: mounting the Docker socket into the container would be root-equivalent and
would violate BOLD Rule 1. Instead the container only writes a **request file**;
the spooler validates it and builds the `docker run` argv itself (non-root
`--user`, explicit free `--gpus` per Rule 2, `${USER}_…` name, workdir-only `-v`,
never `--privileged`). It never runs a command taken from the request on the host.

## Run it

From the repo root on the FLAIR node (the same dir docker-compose runs from):

```bash
COSCIENTIST_SPOOL_DIR_HOST="$PWD/state/jobspool" \
COSCIENTIST_HOST_STATE_ROOT="$PWD/state" \
COSCIENTIST_ALLOWED_IMAGES="${USER}_cojob:dev" \
python3 deploy/flair_spooler.py
```

- `COSCIENTIST_SPOOL_DIR_HOST` — host path of the spool (matches the container's
  `/app/state/jobspool`, i.e. `./state/jobspool`).
- `COSCIENTIST_HOST_STATE_ROOT` — host path of what's mounted at `/app/state`
  (i.e. `$PWD/state`); used to translate a job's workdir to a host `-v` path.
- `COSCIENTIST_ALLOWED_IMAGES` — optional comma-separated allow-list of job
  images. Omit to allow any (still non-root, still sandboxed to the workdir).

The spooler launches jobs as **your** uid:gid (it reads its own — the container
cannot spoof it). Build your job image BOLD-style (`${USER}_cojob:dev`, non-root)
and set `COSCIENTIST_JOB_IMAGE=${USER}_cojob:dev` in `.env` so the agent picks it
by default.

## Run it as a service (survives logout)

`deploy/coscientist-spooler.service` is a user systemd unit template. Edit the
paths/user, then:

```bash
systemctl --user enable --now coscientist-spooler
journalctl --user -u coscientist-spooler -f
```

## Security notes

- The trust boundary is this one script. Read it. It only ever `exec`s `docker`
  with argv it constructs; the request supplies **data**, not commands, and every
  field is validated (`tests/test_longjob_flair.py` covers the guards: path
  escape, GPU pick, argv shape, bad image/name/command).
- Jobs run non-root and are mounted only their own session workdir — they cannot
  see other sessions/users or the host filesystem.
- GPUs are chosen from **free** devices only (Rule 2); if not enough are free the
  job fails fast rather than stomping someone else's (Rule 3 → ask in `#compute`).

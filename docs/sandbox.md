# Sandbox

The concept of a sandbox in Memex is primarily the Docker-backed CMD tool. When you select the Docker CMD tool, commands run inside a container with the workspace mounted at `/workspace`. Other tools such as the `file` tool are effectively sandboxed through filesystem access restrictions to the `base_directory`. We differentiate between them here becuase in most cases where a user will want to modify their sandbox they will be referring to the container used for hte `cmd` tool runs. 

## Selecting the Docker CMD tool

In `config.ini`:

```ini
[TOOLS]
cmd_tool = assistant_docker_tool
docker_env = ephemeral
```

`docker_env` selects a Docker environment section (uppercased), for example `[EPHEMERAL]` or `[WEBDEV]`.

## Docker environment config

In `config.ini`:

```ini
[EPHEMERAL]
docker_image = sandbox-assistant:latest
docker_run_options = --network bridge --memory 512m --cpus=4
persistent = false
tmp_mount = bind
tmp_value = .assistant-tmp
set_tmpdir_env = true
```

Notes:
- `persistent = false` runs `docker run --rm` per command.
- `persistent = true` keeps a container running and uses `docker exec`.
- The base directory is mounted at `/workspace` and used as the working directory.

## Customizing the sandbox image

If you need extra packages available to the CMD tool, build a custom image and point Memex at it.

Starter Dockerfile:
- `examples/Dockerfile`

Example workflow:

```bash
cd examples
docker build -t sandbox-assistant:latest -f Dockerfile .
```

Then set it in your config:

```ini
[EPHEMERAL]
docker_image = sandbox-assistant:latest
```

You can add system packages, language runtimes, or CLI tools to the Dockerfile to match the tasks you want the model to
run through `cmd`.

## Agent mode behavior

By default, agents force ephemeral containers to avoid contention across parallel runs. Control via:

```
[AGENT]
docker_always_ephemeral = True
```

## File tool base_directory guard

The file tool is restricted to `[TOOLS].base_directory` (a path guard), but it does not run inside a container.
See `docs/tools.md` for details on local vs Docker CMD and how the file tool is constrained.

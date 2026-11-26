#!/usr/bin/env bash
# Copy a verified, decrypted cold snapshot into four NEW volumes for an offline drill.
set -euo pipefail
project=${1:?supply a fresh sp029-trial- project}
snapshot=$(realpath -e "${2:?restored snapshot directory required}")
[[ "$project" =~ ^sp029-trial-[a-z0-9-]+$ ]]
[[ -f "$snapshot/deploy/compose.yml" && -f "$snapshot/settings.env" ]]
image=restic/restic:0.18.1@sha256:39d9072fb5651c80d75c7a811612eb60b4c06b32ffe87c2e9f3c7222e1797e76
# Run under exclusive control of the recovery daemon. Interrupted attempts stay intact.
containers=$(docker ps -aq --filter "label=com.docker.compose.project=$project")
networks=$(docker network ls -q --filter "label=com.docker.compose.project=$project")
volumes=$(docker volume ls -q --filter "label=com.docker.compose.project=$project")
[[ -z "$containers$networks$volumes" ]]
names=$(docker volume ls --format '{{.Name}}')
for role in mongo mongo-config postgres identity; do
  [[ -d "$snapshot/data/$role" ]]
  if grep -Fxq "${project}_$role" <<<"$names"; then exit 1; fi
done
for role in mongo mongo-config postgres identity; do
  volume=${project}_$role
  docker volume create --label "com.docker.compose.project=$project" \
    --label "com.docker.compose.volume=$role" "$volume" >/dev/null
  docker run --rm --network none --read-only --entrypoint sh \
    --mount "type=bind,src=$snapshot/data/$role,dst=/from,readonly" \
    --mount "type=volume,src=$volume,dst=/to,volume-nocopy" \
    "$image" -ec 'test -z "$(ls -A /to)"; cp -a /from/. /to/'
done

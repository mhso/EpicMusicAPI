pushd $(dirname $0)

port=5007

# Clean up old image
podman stop -i epic_music_api
podman rm -i epic_music_api

# Build latest image and run container
podman build . -t epic_music_api:latest --env PORT=$port
podman image prune -f

podman run \
    --name epic_music_api \
    -i \
    -t \
    -p $port:$port \
    -m 6500m \
    --memory-reservation 3g \
    -v ./log:/epic_music/log \
    -v ./resources/database:/epic_music/resources/database \
    epic_music_api:latest

popd
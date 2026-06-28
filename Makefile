# Publishing the Docker image (see INTERNAL.md).
# VERSION is read from __version__ in the script, so it stays in sync with the
# git tag (e.g. v1.0.0 -> image tag 1.0.0).

IMAGE   := sandrotosi/simple_transmission_exporter
VERSION := $(shell sed -n "s/^__version__ = '\(.*\)'/\1/p" simple_transmission_exporter.py)

.PHONY: build push publish login version

# Build the image, tagging it with both the current version and latest.
build:
	docker build -t $(IMAGE):$(VERSION) -t $(IMAGE):latest .

# Push both tags to the registry (run `make login` first if needed).
push:
	docker push $(IMAGE):$(VERSION)
	docker push $(IMAGE):latest

# Build then push.
publish: build push

login:
	docker login

# Print the version that would be published.
version:
	@echo $(VERSION)

COMPONENT := custom_components/ha_gateway
# Pinned on purpose: this protoc emits Python code without a runtime version
# check, so the generated module loads with whatever protobuf Home Assistant pins.
GRPC_TOOLS := grpcio-tools==1.62.3

.PHONY: proto test zip

# Regenerates tunnel_pb2.py from proto/. The protocol's source of truth is the
# gateway repository: copy its proto/hagateway/v1/tunnel.proto here first.
proto:
	uv run --no-project --python 3.12 --with $(GRPC_TOOLS) python -m grpc_tools.protoc \
		-Iproto/hagateway/v1 --python_out=$(COMPONENT) tunnel.proto

# The setup flow inside a real Home Assistant, against a real gateway binary.
# Set GATEWAY_BINARY, or build the gateway in ../home-assistant-gateway.
test:
	uv run --no-project --with pytest-homeassistant-custom-component --with grpcio --with protobuf pytest -q

# The integration as HACS installs it: dist/ha_gateway.zip, with the
# integration's files at the root of the archive. Attached to each release.
zip:
	@python3 -c "import json,re,sys; m=json.load(open('$(COMPONENT)/manifest.json'))['version']; c=re.search(r'VERSION = \"(.+)\"', open('$(COMPONENT)/const.py').read()).group(1); sys.exit(0 if m==c else 'manifest.json says %s but const.py says %s' % (m,c))"
	mkdir -p dist
	rm -f dist/ha_gateway.zip
	cd $(COMPONENT) && zip -q -r -X $(CURDIR)/dist/ha_gateway.zip . -x '__pycache__/*' -x '*/__pycache__/*' -x '*.pyc'
	@unzip -l dist/ha_gateway.zip | tail -1

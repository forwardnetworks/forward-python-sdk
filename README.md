# forward-sdk

Official Python SDK for the [Forward Networks](https://www.forwardnetworks.com/) REST API.

> Status: pre-release. The public API is not yet stable.

```python
from forward_sdk import ForwardClient, QueryRef

client = ForwardClient.from_env()  # FORWARD_URL, FORWARD_USERNAME, FORWARD_PASSWORD
for network in client.networks.list():
    print(network.id, network.name)

rows = client.nqe.query(QueryRef.inline("foreach d in network.devices select {name: d.name}"), network_id="101")
```

See the [documentation](https://forwardnetworks.github.io/forward-python-sdk/) for details.

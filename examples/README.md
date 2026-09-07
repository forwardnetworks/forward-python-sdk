# Examples

Runnable scripts. Each reads its connection settings from the environment:

```bash
export FORWARD_URL=https://fwd.app
export FORWARD_USERNAME=your-token-access-key
export FORWARD_PASSWORD=your-token-secret
export FORWARD_NETWORK_ID=101
```

| Script | Shows |
| --- | --- |
| `list_networks.py` | Connecting, and listing networks and snapshots |
| `run_query.py` | Running an NQE query and reading rows |
| `stream_large_query.py` | Streaming a large result set without buffering it |
| `device_inventory.py` | Paging through devices and writing a CSV |
| `publish_queries.py` | Publishing `.nqe` files to the query library |
| `ask_forward_ai.py` | Asking Forward AI a question, and handling not having it |
| `async_client.py` | The same work with the asynchronous client |

from nesa_claimer import NesaClient


def test_find_nodes_collects_multiple_pages_and_exact_public_key(monkeypatch):
    client = NesaClient()
    public_key = "02" + "11" * 32
    page_one = [
        {"node_id": f"node-{index}", "public_key": public_key}
        for index in range(20)
    ]
    page_two = [
        {"node_id": "node-20", "public_key": public_key},
        {"node_id": "wrong-key", "public_key": "03" + "22" * 32},
    ]
    responses = iter(
        [
            {"list": page_one, "total_count": 22},
            {"list": page_two, "total_count": 22},
        ]
    )
    monkeypatch.setattr(client, "_get", lambda *args, **kwargs: next(responses))
    nodes = client.find_nodes(public_key)
    assert len(nodes) == 21
    assert {item["node_id"] for item in nodes} == {
        *(f"node-{index}" for index in range(21))
    }

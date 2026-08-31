from openrag_lab.metadata import dify_metadata_to_openrag_filter


def test_dify_metadata_to_openrag_filter() -> None:
    filt = dify_metadata_to_openrag_filter({"year": "2025", "doc_type": "规范"})

    assert filt.logical_operator == "and"
    assert len(filt.conditions) == 2
    assert filt.to_dict()["conditions"][0] == {
        "name": "year",
        "comparison_operator": "is",
        "value": "2025",
    }

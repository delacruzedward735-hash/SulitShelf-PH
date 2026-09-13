"""Regression coverage for an intentionally empty public catalog."""


def test_empty_catalog_uses_truthful_launch_state_and_hides_zero_pick_campaigns(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"The mall is ready for its first real picks." in response.data
    assert b"No public products have been published yet." in response.data
    assert b"No products match this shelf yet." not in response.data
    assert b"0 picks" not in response.data


def test_empty_filtered_result_remains_a_filter_message(client):
    response = client.get("/?q=definitely-not-a-real-product")

    assert response.status_code == 200
    assert b"No products match these filters." in response.data
    assert b"Clear filters" in response.data
    assert b"The mall is ready for its first real picks." not in response.data

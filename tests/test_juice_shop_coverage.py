from scripts.juice_shop_coverage import canonical_coverage_path, filter_expected


def test_canonical_coverage_path_treats_placeholder_names_as_equivalent():
    assert canonical_coverage_path("/rest/continue-code/apply/{continueCode}") == (
        "/rest/continue-code/apply/{id}"
    )
    assert canonical_coverage_path("/rest/basket/{basketId}/coupon/{coupon}") == (
        "/rest/basket/{id}/coupon/{id}"
    )


def test_filter_expected_public_only():
    expected = {
        "/api/Products",
        "/api/Cards",
        "/rest/user/whoami",
        "/rest/wallet/balance",
    }

    assert filter_expected(expected, public_only=True, get_only=False) == {
        "/api/Products",
        "/rest/user/whoami",
    }


def test_filter_expected_get_only_implies_public_get_subset():
    expected = {
        "/api/Products",
        "/rest/chatbot/respond",
        "/rest/user/whoami",
    }

    assert filter_expected(expected, public_only=False, get_only=True) == {
        "/api/Products",
        "/rest/user/whoami",
    }

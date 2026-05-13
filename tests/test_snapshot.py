from orbis.agent.snapshot import dom_signature_from_items


def test_dom_signature_is_stable_for_equivalent_text_spacing():
    left = dom_signature_from_items([
        ["button", "", "  Save   Item "],
        ["a", "menuitem", "/#/admin"],
    ])
    right = dom_signature_from_items([
        ["BUTTON", "", "save item"],
        ["a", "menuitem", "/#/admin"],
    ])

    assert left == right


def test_dom_signature_changes_when_visible_controls_change():
    left = dom_signature_from_items([["button", "", "Save"]])
    right = dom_signature_from_items([["button", "", "Delete"]])

    assert left != right

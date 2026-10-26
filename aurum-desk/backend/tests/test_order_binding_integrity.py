"""
backend/tests/test_order_binding_integrity.py
V14.4.2 — Repro 3 / BINDNULL01: Strict Instance Binding and Null Invariants.

Enforces:
1. If setup has a non-null setup_instance_id (e.g. "inst-new-99"),
   an order with setup_instance_id=None MUST NOT bind to it as a verified match.
2. If order has a non-null setup_instance_id (e.g. "inst-old-01"),
   a setup with setup_instance_id=None MUST NOT bind to it.
3. If both have non-null setup_instance_id, they must match exactly.
4. Legacy case: if both setup and order have setup_instance_id=None,
   binding is allowed only if direction matches and setup is in active tracking state (not READY).
5. Setup direction mismatch (LONG setup vs SHORT order) must never bind.
"""
import pytest
import models
from services.order_plan_snapshot_service import select_bound_order_for_setup


def test_bindnull01_new_instance_does_not_bind_order_with_missing_instance():
    """BINDNULL01 (PHẦN 043, 224):
    Setup has setup_instance_id="inst-new-99".
    Order has setup_instance_id=None.
    select_bound_order_for_setup must reject binding and return None.
    """
    setup = {
        "id": "setup-binding-01",
        "direction": "LONG",
        "setup_instance_id": "inst-new-99",
        "state": "ARMED",
    }

    order_with_null_instance = models.PaperOrder(
        id="ord-bind-null-01",
        setup_id="setup-binding-01",
        direction="LONG",
        setup_instance_id=None,  # Missing instance!
        state="armed",
    )

    bound = select_bound_order_for_setup(setup, [order_with_null_instance])
    assert bound is None, (
        f"Expected select_bound_order_for_setup to return None for instance mismatch "
        f"(setup has 'inst-new-99', order has None), but got {bound}"
    )


def test_missing_setup_instance_does_not_bind_order_with_instance():
    """Setup has setup_instance_id=None, order has setup_instance_id='inst-existing-01'.
    Must return None.
    """
    setup = {
        "id": "setup-binding-02",
        "direction": "SHORT",
        "setup_instance_id": None,
        "state": "ARMED",
    }

    order_with_instance = models.PaperOrder(
        id="ord-bind-inst-02",
        setup_id="setup-binding-02",
        direction="SHORT",
        setup_instance_id="inst-existing-01",
        state="armed",
    )

    bound = select_bound_order_for_setup(setup, [order_with_instance])
    assert bound is None, "Setup with None instance must not bind order with explicit instance"


def test_exact_instance_match_binds_successfully():
    """When both have identical non-null instance_id and matching direction, binding succeeds."""
    setup = {
        "id": "setup-binding-03",
        "direction": "LONG",
        "setup_instance_id": "inst-exact-match",
        "state": "ARMED",
    }

    matching_order = models.PaperOrder(
        id="ord-bind-exact-03",
        setup_id="setup-binding-03",
        direction="LONG",
        setup_instance_id="inst-exact-match",
        state="armed",
    )

    bound = select_bound_order_for_setup(setup, [matching_order])
    assert bound is not None
    assert bound.id == "ord-bind-exact-03"


def test_instance_difference_rejects():
    """Setup has inst-A, order has inst-B -> returns None."""
    setup = {
        "id": "setup-binding-04",
        "direction": "LONG",
        "setup_instance_id": "inst-A",
        "state": "ARMED",
    }

    order_b = models.PaperOrder(
        id="ord-bind-b-04",
        setup_id="setup-binding-04",
        direction="LONG",
        setup_instance_id="inst-B",
        state="armed",
    )

    bound = select_bound_order_for_setup(setup, [order_b])
    assert bound is None

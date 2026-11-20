"""Prompt composition tests."""

from services.agent import compose_prompt


def test_prompt_only():
    assert compose_prompt("hello", None) == "hello"


def test_salesforce_id_only():
    assert compose_prompt(None, "001xx000003DGb2") == "Salesforce ID: 001xx000003DGb2"


def test_prompt_and_salesforce_id():
    assert (
        compose_prompt("summarize this", "001xx000003DGb2")
        == "summarize this\n\nSalesforce ID: 001xx000003DGb2"
    )

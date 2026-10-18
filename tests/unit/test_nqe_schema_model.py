"""The NQE data-model tree as Forward sends it, deprecation flags included."""

from forward_sdk.models import AnalysisNqeInlinedType


def test_a_type_is_flagged_deprecated_with_a_boolean_and_a_field_with_advice() -> None:
    """Both shapes were seen in one live response; the type's is a flag."""
    node = AnalysisNqeInlinedType.model_validate(
        {
            "type": "Record",
            "deprecated": True,
            "fields": [
                {
                    "name": "uptimeSeconds",
                    "deprecated": "Use uptime instead.",
                    "type": {"type": "Integer"},
                }
            ],
        }
    )
    assert node.deprecated is True
    assert node.fields is not None
    assert node.fields[0].deprecated == "Use uptime instead."

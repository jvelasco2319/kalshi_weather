"""
schema.py

Defines and validates the weather dataset schema.
"""


REQUIRED_METADATA_FIELDS = [
    "location",
    "target_date",
    "variable"
]


REQUIRED_MODEL_FIELDS = [
    "mean",
    "std",
    "distribution"
]


def validate_metadata(metadata):
    """
    Validate dataset metadata.
    """

    missing = []

    for field in REQUIRED_METADATA_FIELDS:

        if field not in metadata:
            missing.append(field)


    if missing:
        raise ValueError(
            f"Missing metadata fields: {missing}"
        )


def validate_model_output(model_name, model_data):
    """
    Validate individual model output.
    """

    missing = []

    for field in REQUIRED_MODEL_FIELDS:

        if field not in model_data:
            missing.append(field)


    if missing:
        raise ValueError(
            f"{model_name} missing fields: {missing}"
        )


    if not isinstance(
        model_data["distribution"],
        dict
    ):
        raise TypeError(
            f"{model_name} distribution must be dict"
        )


def validate_dataset(dataset):
    """
    Validate complete dataset.
    """

    if "metadata" not in dataset:
        raise ValueError(
            "Dataset missing metadata"
        )


    if "models" not in dataset:
        raise ValueError(
            "Dataset missing models"
        )


    validate_metadata(
        dataset["metadata"]
    )


    for model_name, model_data in dataset["models"].items():

        validate_model_output(
            model_name,
            model_data
        )


    return True
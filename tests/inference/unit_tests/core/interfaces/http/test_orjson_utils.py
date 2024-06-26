import base64
from typing import Any

import cv2
import numpy as np
import pytest

from inference.core.interfaces.http.orjson_utils import (
    contains_image,
    serialise_image,
    serialise_list,
    serialise_workflow_result,
)


def test_serialise_image() -> None:
    # given
    np_image = np.zeros((192, 168, 3), dtype=np.uint8)
    image = {
        "type": "numpy_object",
        "value": np_image,
    }

    # when
    result = serialise_image(image=image)

    # then
    assert result["type"] == "base64", "Type of image must point base64"
    decoded = base64.b64decode(result["value"])
    recovered_image = cv2.imdecode(
        np.fromstring(decoded, dtype=np.uint8),
        cv2.IMREAD_UNCHANGED,
    )
    assert (
        recovered_image == np_image
    ).all(), "Recovered image should be equal to input image"


def test_contains_image_when_element_contains_image() -> None:
    # given
    image = {
        "type": "numpy_object",
        "value": np.zeros((192, 168, 3), dtype=np.uint8),
    }

    # when
    result = contains_image(element=image)

    # then
    assert result is True


@pytest.mark.parametrize(
    "image",
    [
        {
            "type": "url",
            "value": "https://some.com/image.jpg",
        },
        [],
        3,
        "some",
    ],
)
def test_contains_image_when_element_does_not_contain_image(image: Any) -> None:
    # when
    result = contains_image(element=image)

    # then
    assert result is False


def test_serialise_list() -> None:
    # given
    np_image = np.zeros((192, 168, 3), dtype=np.uint8)
    elements = [
        3,
        "some",
        {
            "type": "url",
            "value": "https://some.com/image.jpg",
        },
        {
            "type": "numpy_object",
            "value": np_image,
        },
    ]

    # when
    result = serialise_list(elements=elements)

    # then
    assert len(result) == 4, "The same number of elements must be returned"
    assert result[0] == 3, "First element of list must be untouched"
    assert result[1] == "some", "Second element of list must be untouched"
    assert result[2] == {
        "type": "url",
        "value": "https://some.com/image.jpg",
    }, "Third element of list must be untouched"
    assert (
        result[3]["type"] == "base64"
    ), "Type of forth element must be changed into base64"
    decoded = base64.b64decode(result[3]["value"])
    recovered_image = cv2.imdecode(
        np.fromstring(decoded, dtype=np.uint8),
        cv2.IMREAD_UNCHANGED,
    )
    assert (
        recovered_image == np_image
    ).all(), "Recovered image should be equal to input image"


def test_serialise_workflow_result() -> None:
    # given
    np_image = np.zeros((192, 168, 3), dtype=np.uint8)
    workflow_result = {
        "some": [{"detection": 1}],
        "other": {
            "type": "numpy_object",
            "value": np_image,
        },
        "third": [
            "some",
            {
                "type": "numpy_object",
                "value": np_image,
            },
        ],
        "fourth": "to_be_excluded",
        "fifth": {
            "some": "value",
            "my_image": {
                "type": "numpy_object",
                "value": np_image,
            },
        },
        "sixth": ["some", 1, [2, {"type": "numpy_object", "value": np_image}]],
    }

    # when
    result = serialise_workflow_result(
        result=workflow_result, excluded_fields=["fourth"]
    )

    # then
    assert (
        len(result) == 5
    ), "Size of dictionary must be 5, one field should be excluded"
    assert result["some"] == [{"detection": 1}], "Element must not change"
    assert (
        result["other"]["type"] == "base64"
    ), "Type of this element must change due to serialisation"
    assert (
        result["third"][0] == "some"
    ), "This element must not be touched by serialistaion"
    assert (
        result["third"][1]["type"] == "base64"
    ), "Type of this element must change due to serialisation"
    assert (
        result["fifth"]["some"] == "value"
    ), "some key of fifth element is not to be changed"
    assert (
        result["fifth"]["my_image"]["type"] == "base64"
    ), "my_image key of fifth element is to be serialised"
    assert len(result["sixth"]) == 3, "Number of element in sixth list to be preserved"
    assert result["sixth"][:2] == ["some", 1], "First two elements not to be changed"
    assert result["sixth"][2][0] == 2, "First element of nested list not to be changed"
    assert (
        result["sixth"][2][1]["type"] == "base64"
    ), "Second element of nested list to be serialised"

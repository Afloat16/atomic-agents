from pathlib import Path

import pytest
from instructor.processing.multimodal import Image, PDF

from atomic_agents import BaseIOSchema
from atomic_agents.context import ChatHistory


class CloudAttachments(BaseIOSchema):
    """A saved message whose attachments can live in object storage."""

    images: list[Image]
    documents: list[PDF]


def round_trip(content: CloudAttachments) -> ChatHistory:
    history = ChatHistory()
    history.add_message("user", content)
    restored = ChatHistory()
    restored.load(history.dump())
    return restored


def test_s3_pdf_stays_usable_by_bedrock_after_history_round_trip():
    uri = "s3://reports-bucket/reports//annual%20report.pdf"
    document = PDF(source=uri, media_type="application/pdf")
    expected = document.to_bedrock()
    assert expected["document"]["source"] == {"s3Location": {"uri": uri}}

    restored = round_trip(CloudAttachments(images=[], documents=[document]))
    parts = restored.get_history()[0]["content"]
    restored_document = next(part for part in parts if isinstance(part, PDF))

    assert restored_document.to_bedrock() == expected
    assert restored_document.source == uri
    assert document.source == uri


@pytest.mark.parametrize("uri", ["gs://media-bucket/images//scan.png", "s3://media-bucket/images//scan.png"])
def test_cloud_image_and_pdf_source_strings_are_not_normalized_as_paths(uri):
    document_uri = uri.removesuffix(".png") + ".pdf"
    content = CloudAttachments(
        images=[Image(source=uri, media_type="image/png", data="cGl4ZWxz")],
        documents=[PDF(source=document_uri, media_type="application/pdf", data="JVBERi0=")],
    )

    restored = round_trip(content).history[0].content

    assert restored.images[0].source == uri
    assert restored.documents[0].source == document_uri
    assert restored.images[0].data == content.images[0].data
    assert restored.documents[0].data == content.documents[0].data


@pytest.mark.parametrize("source", ["images/scan.png", "s3-report.png", "gs-report.png"])
def test_local_media_sources_still_restore_as_paths(source):
    content = CloudAttachments(images=[Image(source=source, media_type="image/png")], documents=[])

    restored = round_trip(content).history[0].content

    assert restored.images[0].source == Path(source)

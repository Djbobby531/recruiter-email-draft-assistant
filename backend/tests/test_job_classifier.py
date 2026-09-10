from __future__ import annotations

from app.services import job_classifier
from tests.test_pipeline import SAMPLE_BODY, SAMPLE_SUBJECT


def test_job_email_classified_as_job():
    result = job_classifier.classify(SAMPLE_SUBJECT, SAMPLE_BODY, threshold=0.5, ai_provider=None)
    assert result.is_job_email is True


def test_promotion_email_classified_as_not_job():
    result = job_classifier.classify(
        "50% off your next order!",
        "Huge sale this weekend only. Unsubscribe here. Visit our store.",
        threshold=0.5,
        ai_provider=None,
    )
    assert result.is_job_email is False

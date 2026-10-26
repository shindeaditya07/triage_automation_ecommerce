"""The keyword baseline as a system under test (feature/p1-keyword-baseline).

Routing is the keyword backstop plus "intent is other". The baseline reads text only, so it never
predicts a suggestion-only draft and cannot know about a missing order, a mismatched order, a high
value order or a repeat complaint. It is reported next to the model but never gates the run: its
job is to show how much the model adds.
"""

from evals import corpus
from evals.metrics import Prediction
from evals.schema import LabeledTicket, Outcome
from services.api.escalation.keyword_backstop import scan
from services.api.models.enums import Intent
from services.api.pipeline.baseline_classifier import classify


class BaselinePipeline:
    name = "keyword-baseline"
    gated = False

    def predict(self, ticket: LabeledTicket) -> Prediction:
        body = corpus.resolve(ticket.body, ticket.scenario_case)
        backstop = scan(ticket.subject, body)
        result = classify(ticket.subject, body, backstop)
        reasons = backstop.reasons
        if result.intent is Intent.OTHER:
            reasons = [*reasons, "intent_other"]
        return Prediction(
            intent=result.intent,
            urgency=result.urgency.value,
            outcome=Outcome.NO_DRAFT_ESCALATION if reasons else Outcome.DRAFT,
            escalation_reasons=reasons,
        )

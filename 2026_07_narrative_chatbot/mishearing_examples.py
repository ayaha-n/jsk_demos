"""Few-shot examples for technical-term extraction and mishearing planning."""

import dspy

from mishearing_cases import candidate_for
from pooh_examples import INITIAL_SITUATION


MISHEARING_EXAMPLES = [
    dspy.Example(
        current_situation=INITIAL_SITUATION,
        user_utterance="君はロボットだよね",
        history="",
        technical_terms=["ロボット"],
        candidates=[candidate_for("ロボット")],
    ).with_inputs("current_situation", "user_utterance", "technical_terms", "history"),
    dspy.Example(
        current_situation=INITIAL_SITUATION,
        user_utterance="モータはどこについてるの？",
        history="",
        technical_terms=["モータ"],
        candidates=[candidate_for("モーター")],
    ).with_inputs("current_situation", "user_utterance", "technical_terms", "history"),
    dspy.Example(
        current_situation=INITIAL_SITUATION,
        user_utterance="アクチュエータは何を使っているの？",
        history="",
        technical_terms=["アクチュエータ"],
        candidates=[candidate_for("アクチュエータ")],
    ).with_inputs("current_situation", "user_utterance", "technical_terms", "history"),
]

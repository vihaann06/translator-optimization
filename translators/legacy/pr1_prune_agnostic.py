"""PR #1 round-5 pruner (huskydj1/webpage-translator) applied to the baseline outline, with no task text or
history, so its task-word rule never fires and the output depends only on the page."""
from translators.baseline_axtree import translate as baseline
from translators.legacy.pr1.pruner import prune


def translate(state: dict) -> dict:
    return prune(baseline(state), "", [])

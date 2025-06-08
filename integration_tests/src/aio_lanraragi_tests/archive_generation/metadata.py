import numpy as np
from typing import Callable, List

from aio_lanraragi_tests.archive_generation.models import TagGenerator

def default_tag_id_to_name(tag_id: int) -> str:
    return f"tag-{tag_id}"

def create_tag_generators(n: int, pmf: Callable[[float], float], tag_id_to_name: Callable[[int], str]=default_tag_id_to_name) -> List[TagGenerator]:
    """
    Create n tag generators with an assignment probability given by an 
    arbitrary probability mass function.

    The PMF must be defined on [0, 1] with range [0, 1]. It does not need to be weighted.
    No checks will be made for PMF conditions.
    """

    return [TagGenerator(tag_id_to_name(tag_id), pmf(t)) for tag_id, t in enumerate(np.linspace(0, 1, n))]

def get_tag_assignments(tag_generators: List[TagGenerator], generator: np.random.Generator=None) -> List[str]:
    """
    Takes a list of tag generators and returns a subset of tag strings based on assignment probability.
    
    Generator is for reproducibility.
    """
    tags = []
    for tg in tag_generators:
        add_to_tags = generator.binomial(1, tg.assign_probability, 1)[0] if generator else np.random.binomial(1, tg.assign_probability, 1)[0]
        if add_to_tags:
            tags.append(tg.tag_name)
    return tags
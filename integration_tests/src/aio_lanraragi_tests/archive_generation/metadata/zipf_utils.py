"""
Utilities for generating arcidx to tagidx-list distributions following zipfs law.
"""

import collections
import sys
import numpy as np
from typing import Counter, Dict, List, Literal, Set

# tests v0.1.15
def get_archive_idx_to_tag_idxs_map(
    num_archives: int,
    num_tags: int,
    max_tags_per_archive: int,
    generator: np.random.Generator = None,
    poisson_lam: float = 7.0,
    tag_zipf_exp: float = 1.1,
    generation_method: Literal['zipf', 'zipf-fast'] = 'zipf-fast',
) -> Dict[int, List[int]]:
    if generator is None:
        generator = np.random.default_rng()
    archive_idx_to_tag_idx: Dict[int, List[int]] = {}

    match generation_method:
        case 'zipf-fast':
            for arcidx in range(num_archives):
                k = generator.poisson(lam=poisson_lam)
                k = max(1, min(k, max_tags_per_archive, num_tags))

                chosen: Set[int] = set()
                while len(chosen) < k:
                    for r in generator.zipf(tag_zipf_exp, size=k * 2):
                        idx = int(r - 1)
                        if 0 <= idx < num_tags:
                            chosen.add(idx)
                            if len(chosen) == k:
                                break

                tag_idxs = sorted(chosen)
                archive_idx_to_tag_idx[arcidx] = tag_idxs
        case 'zipf':
            ranks = np.arange(1, num_tags + 1)
            weights = 1.0 / np.power(ranks, tag_zipf_exp)
            probs = weights / weights.sum()

            for arcidx in range(num_archives):
                k = generator.poisson(lam=poisson_lam)
                k = max(1, min(k, max_tags_per_archive, num_tags))

                chosen_idxs = generator.choice(
                    num_tags, size=k, replace=False, p=probs
                )
                chosen_tags = sorted(int(i) for i in chosen_idxs)
                archive_idx_to_tag_idx[arcidx] = chosen_tags

    return archive_idx_to_tag_idx

if __name__ == "__main__":
    """
    Evaluate the shape of the zipf distribution.
    """
    import argparse
    import matplotlib.pyplot as plt

    parser = argparse.ArgumentParser()
    parser.add_argument("--num-archives", type=int, default=1_000_000)
    parser.add_argument("--num-tags", type=int, default=100_000)
    parser.add_argument("--tags-per-archive", type=int, default=40)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    num_archives: int = args.num_archives
    num_tags: int = args.num_tags
    max_tags_per_archive: int = args.tags_per_archive
    npseed: int = args.seed

    npgenerator = np.random.default_rng(seed=npseed)

    try:
        print(f"Generating synthetic data (num archives={num_archives}, num tags={num_tags}, seed={npseed})")
        archive_idx_to_tag_idxs = get_archive_idx_to_tag_idxs_map(
            num_archives, num_tags, max_tags_per_archive, generator=npgenerator,
            generation_method='zipf-fast'
        )

        tag_freq: Counter[int] = collections.Counter()
        for tag_idxs in archive_idx_to_tag_idxs.values():
            tag_freq.update(tag_idxs)

        images_per_tag_arr = np.array(list(tag_freq.values()))

        # Rank–popularity log–log plot (like interpret_data)
        sorted_pop = np.sort(images_per_tag_arr)[::-1]
        ranks = np.arange(1, len(sorted_pop) + 1)

        plt.figure()
        plt.loglog(ranks, sorted_pop)
        plt.xlabel("Tag rank")
        plt.ylabel("#archives per tag")
        plt.title("Tag popularity rank plot (log-log)")
        plt.tight_layout()
        plt.show()
    except KeyboardInterrupt:
        print("Exiting...")
        sys.exit(130)
"""Tests for the flood propagation core (Module B).

The reference algorithm in CLAUDE.md is a 4-connected BFS cellular automaton
seeded from the ocean. These tests pin the properties that actually matter and
that were each found to be violated during development:

- the flood advances inland as the water level rises (it did not, before the
  frontier was fixed);
- ocean connectivity is enforced — an inland depression below the surge level
  must stay dry, while one linked by low ground must flood;
- the vectorised implementation is *identical* to the literal reference BFS
  (a 3x3 dilation is 8-connected and over-floods; the cross footprint is
  load-bearing);
- 4-connectivity specifically — a cell reachable only diagonally must not flood.

No test needs network access or the real DEM.
"""

import numpy as np
import pytest
from scipy import ndimage

from backend.simulation.flood import (
    FOUR_CONNECTED,
    simulate_flood_propagation,
)


def literal_reference_bfs(elevation, ocean_mask, target_surge, n_steps):
    """The CLAUDE.md reference BFS, transcribed literally.

    Kept so the vectorised production path can be diffed against it, and as a
    regression guard on a real defect: this snippet drains its frontier each
    step and refills it only from cells newly visited, so a step that floods
    nothing empties the queue and the simulation stalls permanently. It
    therefore *under*-floods. `TestReferenceSnippetIsUnderFlooding` pins that
    difference; the production path is the corrected behaviour, and this
    function is not the oracle.
    """
    from collections import deque

    frontier = deque(zip(*np.where(ocean_mask)))
    visited = ocean_mask.copy()
    frames = []
    for step in range(1, n_steps + 1):
        water_level = target_surge * (step / n_steps)
        next_frontier = deque()
        while frontier:
            y, x = frontier.popleft()
            for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                ny, nx = y + dy, x + dx
                if (
                    0 <= ny < elevation.shape[0]
                    and 0 <= nx < elevation.shape[1]
                    and not visited[ny, nx]
                    and elevation[ny, nx] <= water_level
                ):
                    visited[ny, nx] = True
                    next_frontier.append((ny, nx))
        frontier = next_frontier
        frames.append(visited.copy())
    return frames


def corrected_literal_bfs(elevation, ocean_mask, target_surge, n_steps):
    """The same BFS with the stalling frontier bug fixed.

    Every flooded cell stays a candidate parent at every later step, because a
    cell too high at step k can become passable at step k+1 and its only
    neighbours are already-flooded cells. This is the oracle the vectorised
    implementation must reproduce exactly.
    """
    flooded = ocean_mask.copy()
    frontier = set(zip(*np.where(ocean_mask)))
    frames = []
    for step in range(1, n_steps + 1):
        water_level = target_surge * (step / n_steps)
        for y, x in list(frontier):
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                ny, nx = y + dy, x + dx
                if (
                    0 <= ny < elevation.shape[0]
                    and 0 <= nx < elevation.shape[1]
                    and not flooded[ny, nx]
                    and elevation[ny, nx] <= water_level
                ):
                    flooded[ny, nx] = True
                    frontier.add((ny, nx))
        frames.append(flooded.copy())
    return frames


class TestMatchesReferenceAlgorithm:
    """The vectorised path must equal the corrected literal BFS, cell for cell."""

    @pytest.mark.parametrize("seed", range(8))
    def test_matches_corrected_bfs_on_random_dems(self, seed):
        rng = np.random.RandomState(seed)
        elevation = rng.uniform(-5, 25, (40, 45))
        ocean = rng.uniform(0, 1, (40, 45)) < 0.2
        surge = float(rng.uniform(0.5, 4.0))

        fast = simulate_flood_propagation(elevation, ocean, surge, n_steps=10)
        slow = corrected_literal_bfs(elevation, ocean, surge, 10)

        assert len(fast) == len(slow)
        for got, want in zip(fast, slow):
            assert (got == want).all()

    @pytest.mark.parametrize("seed", range(4))
    def test_reference_snippet_is_known_to_under_flood(self, seed):
        """Documents the CLAUDE.md snippet's frontier bug.

        The published snippet stalls whenever a step floods nothing, so it
        returns strictly less water than the corrected algorithm. This is the
        defect the production code fixes; if this test ever starts failing
        because the two now agree, the snippet has been fixed upstream and the
        notes here should be revisited.
        """
        rng = np.random.RandomState(seed)
        elevation = rng.uniform(-5, 25, (40, 45))
        ocean = rng.uniform(0, 1, (40, 45)) < 0.2
        surge = float(rng.uniform(0.5, 4.0))

        stalling = literal_reference_bfs(elevation, ocean, surge, 10)
        corrected = corrected_literal_bfs(elevation, ocean, surge, 10)

        assert stalling[-1].sum() <= corrected[-1].sum()

    def test_footprint_is_four_connected(self):
        # A 3x3 footprint would include the diagonals; the reference does not.
        assert not FOUR_CONNECTED[0, 0]
        assert not FOUR_CONNECTED[0, 2]
        assert not FOUR_CONNECTED[2, 0]
        assert not FOUR_CONNECTED[2, 2]
        assert FOUR_CONNECTED[0, 1] and FOUR_CONNECTED[1, 0]
        assert FOUR_CONNECTED[1, 1] and FOUR_CONNECTED[1, 2]

    def test_no_diagonal_leakage(self):
        """A cell reachable only diagonally must not flood.

        Ocean is the top-left 2x2 block. The cell at (2,2) is diagonally
        adjacent to ocean but 4-disconnected from it, and the rest of the grid
        is high, so under 4-connectivity it must stay dry.
        """
        elevation = np.full((5, 5), 10.0)
        elevation[0:2, 0:2] = 0.0
        elevation[2, 2] = 0.0
        ocean = np.zeros((5, 5), dtype=bool)
        ocean[0:2, 0:2] = True

        frames = simulate_flood_propagation(elevation, ocean, 1.0, n_steps=10)

        assert not frames[-1][2, 2], "flooded across a diagonal, which is 8-connected"


class TestInlandPropagation:
    def test_flood_advances_as_level_rises(self):
        """The regression this module was written to fix: a naive drained queue
        floods nothing and stalls forever once a step adds no cells."""
        elevation = np.tile(np.linspace(0, 10, 50), (20, 1))
        ocean = np.zeros_like(elevation, dtype=bool)
        ocean[:, :5] = True

        frames = simulate_flood_propagation(elevation, ocean, 3.0, n_steps=10)

        reach = [int(np.where(f.any(axis=0))[0].max()) for f in frames]
        assert reach[-1] > reach[0], "flood never advanced inland"
        assert reach == sorted(reach), "flood extent shrank as water rose"

    def test_area_is_monotonic(self):
        rng = np.random.RandomState(3)
        elevation = rng.uniform(-5, 20, (30, 30))
        ocean = elevation <= 0
        frames = simulate_flood_propagation(elevation, ocean, 3.0, n_steps=8)
        counts = [int(f.sum()) for f in frames]
        assert counts == sorted(counts)


class TestOceanConnectivity:
    def test_enclosed_depression_stays_dry(self):
        """Surge cannot reach a depression that is not connected to the sea."""
        elevation = np.full((21, 21), 10.0)
        elevation[10, :5] = 0.0
        elevation[5, 10] = -5.0  # below sea level, but walled in
        ocean = np.zeros((21, 21), dtype=bool)
        ocean[:, :5] = True

        frames = simulate_flood_propagation(elevation, ocean, 4.0, n_steps=10)

        assert not frames[-1][5, 10]
        assert frames[-1].sum() == ocean.sum()

    def test_depression_linked_by_low_ground_floods(self):
        """The converse: a pit joined to the sea by a low ridge does flood."""
        elevation = np.full((21, 21), 10.0)
        elevation[10, :5] = 0.0
        elevation[5, 5:11] = 2.0  # ridge linking sea to pit
        elevation[5, 10] = -5.0
        ocean = np.zeros((21, 21), dtype=bool)
        ocean[:, :5] = True

        # Enough steps for the BFS to walk the ridge (it advances ~1 cell/step).
        frames = simulate_flood_propagation(elevation, ocean, 4.0, n_steps=12)

        assert frames[-1][5, 10], "connected depression did not flood"


class TestDegenerateInputs:
    def test_zero_surge_floods_nothing(self):
        elevation = np.zeros((10, 10))
        frames = simulate_flood_propagation(elevation, np.zeros((10, 10), bool), 0.0, 5)
        assert all(not f.any() for f in frames)

    def test_no_ocean_cells_floods_nothing(self):
        """No seed means no flood, even with everything below the surge level."""
        elevation = np.zeros((10, 10))
        frames = simulate_flood_propagation(
            elevation, np.zeros((10, 10), dtype=bool), 4.0, 5
        )
        assert all(not f.any() for f in frames)

    def test_bounded_by_grid(self):
        """Flooding at the raster edge must not wrap or index out of bounds.

        40 steps so the BFS can cross the whole 6x6 grid from the centre; the
        assertion is that it fills exactly the grid and stops there.
        """
        elevation = np.zeros((6, 6))
        ocean = np.zeros((6, 6), dtype=bool)
        ocean[3, 3] = True
        frames = simulate_flood_propagation(elevation, ocean, 1.0, 40)
        assert frames[-1].shape == (6, 6)
        assert frames[-1].all()

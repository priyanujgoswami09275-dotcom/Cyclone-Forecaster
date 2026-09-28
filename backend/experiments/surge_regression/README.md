# Retired: the 3-feature surge regression

**Not on the runtime path.** Nothing in `backend/` imports this directory.
`backend/simulation/surge.py` is the live surge model.

Kept because a modelling decision that discards a "trained regression" should
be re-examinable against the code that produced the numbers, not against a
prose summary. This is that record.

## What it was

    surge_m = f(wind_kmph, forward_speed_kmph, approach_angle_flag)

Ordinary least squares (`sklearn.linear_model.LinearRegression`), fitted on
four historical Bay of Bengal cyclones, serialised by `train_surge_model.py`
to `surge_model.pkl` (a joblib dict `{model, features, loo_mae}`) and loaded
per-request behind an `lru_cache`.

Full-fit coefficients:

| term | value |
|---|---|
| `wind_kmph` | +0.0475 |
| `forward_speed_kmph` | +0.6625 |
| `approach_angle_flag` | −2.8625 |
| intercept | −12.0 |

## Why it was retired

**4 rows, 4 parameters.** Three feature columns plus an intercept is four
fitted parameters, against four training rows — an **exactly determined**
fit, with **zero residual degrees of freedom**. The full fit interpolates all
four training points to ~1e-16. A model that cannot be wrong on its own
training data has no redundancy left to generalise with.

**LOOCV MAE 2.36 m**, on a training range of 0.6–2.9 m. The mean flatters
it; per-point:

| point | actual | LOO prediction | abs error | % of actual |
|---|---|---|---|---|
| Remal 2024 | 1.2 m | 2.679 m | 1.479 m | 123.3% |
| Helen 2013 | 1.6 m | 0.019 m | 1.581 m | 98.8% |
| Lehar 2013 | 2.9 m | −0.026 m | 2.926 m | 100.9% |
| Mandous 2021 | 0.6 m | 4.053 m | 3.453 m | **575.4%** |

Every point is wrong by roughly its own magnitude, and the two largest
predictions are physically absurd — a negative surge for Lehar, and 4.05 m
for an event that produced 0.6 m.

**A mean-baseline predictor beats it.** Compared like-for-like under the same
leave-one-out splits, a predictor that ignores its inputs and returns the mean
of the training folds scores an MAE of **0.90 m** — **2.6× better** than the
regression's 2.36 m. A model that loses to ignoring its own inputs has
demonstrated it learned nothing useful from them. (Verified by
`test_the_regression_loses_to_a_mean_baseline`.)

**3 of the 4 rows are unsourced.** The trainer's docstring attributes all
four points to "the four verified real historical cases from CLAUDE.md".
**CLAUDE.md contains no such table** — it names Remal alone. Helen (1.6 m),
Lehar (2.9 m) and Mandous (0.6 m) have no traceable citation anywhere in the
repository. Lehar's 2.9 m is the point that forces the model's extrapolation
to 5 m and beyond.

**2 of the 3 features were never observed.** The app's slider supplies wind
speed only. Forward speed and approach angle were filled from documented-
typical constants (15 km/h, head-on), so the model spent two thirds of its
capacity on assumed inputs.

## What replaced it

A single-anchor quadratic scaling law, in `backend/simulation/surge.py`:

    surge_m = 1.2 m * (wind_kmph / 115 kmph) ** 2

Anchored on Cyclone Remal (May 2024, landfall between Sagar Island and
Khepupara) at its documented midpoint: 110–120 kmph → 1.0–1.5 m.

It is far less expressive than the regression. That is the point. With one
observed event there is nothing to fit, so the honest model has no
parameters to overfit, is monotone in wind by construction, needs no clamp
(it is non-negative and grows smoothly), and reproduces the anchor exactly
rather than approximately.

It also makes the anchor *reachable at all*: the retired model produced
0.062 m at category 5 and 3.863 m at category 6, so the slider jumped
straight past 1.2 m and the app could never display its own case-study
anchor. Under the scaling law 115 kmph gives exactly 1.2 m, exposed as the
`remal_observed` preset in `/categories`.

**What came with it, in order.** Two corrections landed on the category
mapping after the swap, and the second is the one that mattered:

1. Category 6's wind was 185 kmph — the midpoint of an invented 250 kmph
   ceiling. IMD documents Super Cyclonic Storm as open-ended at the top, so
   there was no midpoint to take.
2. **The whole table was in the wrong unit.** Those thresholds — 17, 28, 34,
   48, 64, 90, 120 — are IMD's **knots** column. The code named, labelled and
   compared them as kmph, so every wind in the app was about 1.85x too small.
   Category 6 "started at 120" when IMD's km/h column starts it at **222**.

Between those two, the top of the slider sat at 1.31 m of surge, which only
reaches DEM cells at exactly 1 m elevation: a thin coastal fringe holding no
mapped hospital and shattering into sub-`MIN_PART_KM2` fragments. The flood
drew nothing and `/exposure` returned zero at every category. That was not a
surge-model problem and not a DEM problem — it was a unit bug, one row above
the model. With the correct km/h column, category 6 is 222 kmph / 4.47 m and
the exposure, routing and allocation chains all work again.

**The lesson worth keeping:** the model was replaced because it could not be
validated, and the replacement looked wrong for a whole round. Both the
"185 kmph is invented" finding and the "1.31 m exposes nothing" finding were
real and worth chasing — but the second had a cause one level up that nobody
checked, because the table it came from had always been internally consistent.
A number that has never been wrong can still be wrong. See MEMORY.md
"Flagged for review" §31.

The replacement carries its own limitation, stated in every response: it is a
screening estimate scaled from one observed event, and it omits tide,
atmospheric pressure, bathymetry and storm size. See
MEMORY.md "Flagged for review" for the open decision on this swap.

## Files here

| file | what it is |
|---|---|
| `README.md` | this record |
| `regression.py` | the retired model, callable for side-by-side comparison |
| `train_surge_model.py` | the trainer that produced `surge_model.pkl` |
| `surge_model.pkl` | the fitted artefact (joblib, 761 B) |
| `test_surge_regression.py` | the tests that guarded it |

## Re-running it

    venv/bin/python -m backend.experiments.surge_regression.train_surge_model

Prints the per-point LOOCV table and rewrites `surge_model.pkl` in this
directory. `OUT_PATH` points here rather than at the original
`data/surge_model.pkl` so that re-running cannot quietly recreate a file on
the data path that nothing reads.

"""Statistical tests for the miners: every test returns its effect size, a 95% interval and a
p-value, and the run controls false discoveries across all of them (Benjamini-Hochberg).

Each function takes plain arrays (pulled from DuckDB by the miners) and is deterministic: any
resampling uses the generator it is given, which the run seeds from its id. Tests are chosen to
be robust to outliers and skew (rank-based where a parametric test would assume normality),
and their effect sizes are the ones the facts put into words.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Final

import numpy as np
from numpy.typing import NDArray
from scipy import stats as sps

ALPHA: Final = 0.05  # false-discovery rate across all tests of a run
Z95: Final = 1.959963984540054
BOOTSTRAP_RESAMPLES: Final = 200
BOOTSTRAP_ROWS: Final = 20_000

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
Statistic = Callable[[IntArray, FloatArray], float]


@dataclass(frozen=True, slots=True)
class TestResult:
    """One hypothesis test. ``hypothesis`` is a stable key within the run (the analysis id and
    what was tested); a re-test on other data (sensitivity) carries the same key."""

    hypothesis: str
    method: str  # plain words: which test
    columns: tuple[str, ...]
    n: int
    effect_name: str  # spearman, eta, cramers_v, kendall_tau, mean_difference, ...
    effect: float
    ci_low: float | None
    ci_high: float | None
    p_value: float
    #: The effect on one 0-to-1 scale for ranking: the absolute correlation, eta or V, and
    #: for differences the r-equivalent of the standardised difference, d / sqrt(d^2 + 4).
    size: float = 0.0
    q_value: float | None = None  # filled in by ``benjamini_hochberg``

    @property
    def passed(self) -> bool:
        return self.q_value is not None and self.q_value <= ALPHA


def seed_for(*parts: str) -> int:
    """A 64-bit seed from text (the run id, then the analysis): the same run always draws the
    same shuffles and samples, and different analyses draw independent ones."""
    digest = hashlib.sha256("\x1f".join(parts).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _finite(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def _p(value: float) -> float:
    """A p-value that is always a number in [0, 1] (a degenerate test is not evidence)."""
    return min(1.0, max(0.0, float(value))) if math.isfinite(value) else 1.0


# --------------------------------------------------------------------------- correlation


def spearman(
    hypothesis: str, columns: Sequence[str], x: FloatArray, y: FloatArray
) -> TestResult | None:
    """Spearman's rank correlation with its p-value, and a 95% interval from Fisher's z with
    the Bonett-Wright standard error. ``None`` when either side is constant or n < 4."""
    n = len(x)
    if n < 4 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return None
    result = sps.spearmanr(x, y)
    rho, p = float(np.asarray(result.statistic)), float(np.asarray(result.pvalue))
    if not math.isfinite(rho):
        return None
    low, high = _fisher_interval(rho, n, se=math.sqrt((1 + rho * rho / 2) / (n - 3)))
    return TestResult(
        hypothesis=hypothesis,
        method="Spearman rank correlation",
        columns=tuple(columns),
        n=n,
        effect_name="spearman",
        effect=rho,
        ci_low=low,
        ci_high=high,
        p_value=_p(p),
        size=abs(rho),
    )


def _fisher_interval(r: float, n: int, *, se: float) -> tuple[float, float]:
    del n
    z = math.atanh(max(-0.999999, min(0.999999, r)))
    return math.tanh(z - Z95 * se), math.tanh(z + Z95 * se)


def pooled_within_groups(
    hypothesis: str,
    columns: Sequence[str],
    correlations: Sequence[tuple[float, int]],
) -> TestResult | None:
    """The correlation that holds *within* groups: each group's rank correlation on Fisher's
    z scale, weighted by n - 3 (a fixed-effect pool), with its interval and p-value."""
    usable = [(r, n) for r, n in correlations if n > 3 and math.isfinite(r)]
    if len(usable) < 2:
        return None
    weights = np.array([n - 3 for _, n in usable], dtype=float)
    zs = np.array([math.atanh(max(-0.999999, min(0.999999, r))) for r, _ in usable])
    z = float(np.sum(weights * zs) / np.sum(weights))
    se = 1 / math.sqrt(float(np.sum(weights)))
    p = 2 * sps.norm.sf(abs(z / se))
    return TestResult(
        hypothesis=hypothesis,
        method="Rank correlation within each group, pooled (Fisher's z, fixed effect)",
        columns=tuple(columns),
        n=int(sum(n for _, n in usable)),
        effect_name="pooled_spearman",
        effect=math.tanh(z),
        ci_low=math.tanh(z - Z95 * se),
        ci_high=math.tanh(z + Z95 * se),
        p_value=_p(p),
        size=abs(math.tanh(z)),
    )


def kendall_trend(hypothesis: str, columns: Sequence[str], values: FloatArray) -> TestResult | None:
    """Mann-Kendall trend test on a series in time order: Kendall's tau between position and
    value, with a normal-approximation interval."""
    n = len(values)
    if n < 3 or np.ptp(values) == 0:
        return None
    result = sps.kendalltau(np.arange(n), values)
    tau, p = float(result.statistic), float(result.pvalue)
    if not math.isfinite(tau):
        return None
    se = math.sqrt(2 * (2 * n + 5) / (9 * n * (n - 1)))
    return TestResult(
        hypothesis=hypothesis,
        method="Mann-Kendall trend test over the periods",
        columns=tuple(columns),
        n=n,
        effect_name="kendall_tau",
        effect=tau,
        ci_low=max(-1.0, tau - Z95 * se),
        ci_high=min(1.0, tau + Z95 * se),
        p_value=_p(p),
        size=abs(tau),
    )


# --------------------------------------------------------------------------- groups


def eta(groups: IntArray, values: FloatArray) -> float:
    """The correlation ratio: the square root of the share of the variation in ``values``
    that the groups account for (0 to 1)."""
    if len(values) == 0:
        return 0.0
    counts = np.bincount(groups).astype(float)
    sums = np.bincount(groups, weights=values)
    present = counts > 0
    grand = values.mean()
    ssb = float(np.sum(sums[present] ** 2 / counts[present]) - len(values) * grand * grand)
    sst = float(np.sum((values - grand) ** 2))
    if sst <= 0:
        return 0.0
    return math.sqrt(max(0.0, min(1.0, ssb / sst)))


def group_difference(
    hypothesis: str,
    columns: Sequence[str],
    groups: IntArray,
    values: FloatArray,
    rng: np.random.Generator,
) -> TestResult | None:
    """Do the groups differ? Kruskal-Wallis (rank-based, robust to skew and outliers) for the
    p-value; eta for the size, with a bootstrap interval. Groups are codes 0..k-1."""
    labels = np.unique(groups)
    if len(labels) < 2 or np.ptp(values) == 0:
        return None
    samples = [values[groups == g] for g in labels]
    try:
        _, p = sps.kruskal(*samples)
    except ValueError:  # every value identical
        return None
    size = eta(groups, values)
    low, high = _bootstrap(eta, groups, values, rng)
    return TestResult(
        hypothesis=hypothesis,
        method="Kruskal-Wallis test; size as eta (share of variation the groups account for)",
        columns=tuple(columns),
        n=len(values),
        effect_name="eta",
        effect=size,
        ci_low=low,
        ci_high=high,
        p_value=_p(p),
        size=size,
    )


def _bootstrap(
    statistic: Statistic,
    groups: IntArray,
    values: FloatArray,
    rng: np.random.Generator,
) -> tuple[float | None, float | None]:
    """Percentile interval from resampling rows (at most ``BOOTSTRAP_ROWS`` per resample, so
    for larger tables the interval is wider than the data allows: conservative)."""
    n = len(values)
    if n < 10:
        return None, None
    size = min(n, BOOTSTRAP_ROWS)
    draws = np.empty(BOOTSTRAP_RESAMPLES)
    for i in range(BOOTSTRAP_RESAMPLES):
        pick = rng.integers(0, n, size=size)
        draws[i] = statistic(groups[pick], values[pick])
    low, high = np.quantile(draws, [0.025, 0.975])
    return _finite(float(low)), _finite(float(high))


def cramers_v(table: NDArray[np.float64]) -> float:
    n = float(table.sum())
    k = min(table.shape) - 1
    if n == 0 or k < 1:
        return 0.0
    chi2 = _chi2(table)
    return math.sqrt(max(0.0, chi2 / (n * k)))


def _chi2(table: NDArray[np.float64]) -> float:
    n = table.sum()
    expected = np.outer(table.sum(axis=1), table.sum(axis=0)) / n
    mask = expected > 0
    return float(np.sum((table[mask] - expected[mask]) ** 2 / expected[mask]))


def association(
    hypothesis: str,
    columns: Sequence[str],
    a: IntArray,
    b: IntArray,
    rng: np.random.Generator,
) -> TestResult | None:
    """Are two sets of categories linked? Pearson's chi-squared test of independence, with
    Cramér's V for the size and a bootstrap interval. Inputs are category codes per row."""
    if len(a) < 4:
        return None
    table = _crosstab(a, b)
    if min(table.shape) < 2:
        return None
    chi2, p, _, _ = sps.chi2_contingency(table, correction=False)
    del chi2
    size = cramers_v(table)

    def v(x: IntArray, y: FloatArray) -> float:
        return cramers_v(_crosstab(x, y.astype(np.int64)))

    low, high = _bootstrap(v, a, b.astype(float), rng)
    return TestResult(
        hypothesis=hypothesis,
        method="Chi-squared test of independence; size as Cramér's V",
        columns=tuple(columns),
        n=len(a),
        effect_name="cramers_v",
        effect=size,
        ci_low=low,
        ci_high=high,
        p_value=_p(p),
        size=size,
    )


def _crosstab(a: IntArray, b: IntArray) -> NDArray[np.float64]:
    _, ai = np.unique(a, return_inverse=True)
    _, bi = np.unique(b, return_inverse=True)
    table = np.zeros((int(ai.max()) + 1, int(bi.max()) + 1))
    np.add.at(table, (ai, bi), 1)
    return table


def mean_difference(
    hypothesis: str, columns: Sequence[str], first: FloatArray, second: FloatArray
) -> TestResult | None:
    """Difference of two means (``first`` minus ``second``) with Welch's t-test and interval:
    no equal-variance assumption."""
    n1, n2 = len(first), len(second)
    if n1 < 2 or n2 < 2:
        return None
    v1, v2 = float(np.var(first, ddof=1)), float(np.var(second, ddof=1))
    se2 = v1 / n1 + v2 / n2
    diff = float(first.mean() - second.mean())
    if se2 <= 0:
        return None
    se = math.sqrt(se2)
    df = se2**2 / ((v1 / n1) ** 2 / (n1 - 1) + (v2 / n2) ** 2 / (n2 - 1))
    t = diff / se
    p = 2 * sps.t.sf(abs(t), df)
    crit = float(sps.t.ppf(0.975, df))
    pooled_sd = math.sqrt((v1 + v2) / 2)
    d = diff / pooled_sd if pooled_sd > 0 else 0.0
    return TestResult(
        hypothesis=hypothesis,
        method="Welch's t-test (difference of two means)",
        columns=tuple(columns),
        n=n1 + n2,
        effect_name="mean_difference",
        effect=diff,
        ci_low=diff - crit * se,
        ci_high=diff + crit * se,
        p_value=_p(p),
        size=_r_equivalent(d),
    )


def proportion_difference(
    hypothesis: str, columns: Sequence[str], hits: tuple[int, int], sizes: tuple[int, int]
) -> TestResult | None:
    """Difference of two shares (first minus second): two-proportion z-test for the p-value,
    Newcombe's hybrid Wilson interval for the difference."""
    (x1, x2), (n1, n2) = hits, sizes
    if n1 < 1 or n2 < 1:
        return None
    p1, p2 = x1 / n1, x2 / n2
    pooled = (x1 + x2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    p = 2 * sps.norm.sf(abs((p1 - p2) / se))
    l1, u1 = _wilson(x1, n1)
    l2, u2 = _wilson(x2, n2)
    diff = p1 - p2
    h = 2 * math.asin(math.sqrt(p1)) - 2 * math.asin(math.sqrt(p2))  # Cohen's h
    low = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    high = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return TestResult(
        hypothesis=hypothesis,
        method="Two-proportion z-test (difference of two shares)",
        columns=tuple(columns),
        n=n1 + n2,
        effect_name="share_difference",
        effect=diff,
        ci_low=max(-1.0, low),
        ci_high=min(1.0, high),
        p_value=_p(p),
        size=_r_equivalent(h),
    )


def _r_equivalent(d: float) -> float:
    return abs(d) / math.sqrt(d * d + 4)


def _wilson(x: int, n: int) -> tuple[float, float]:
    p = x / n
    denominator = 1 + Z95 * Z95 / n
    centre = (p + Z95 * Z95 / (2 * n)) / denominator
    half = Z95 * math.sqrt(p * (1 - p) / n + Z95 * Z95 / (4 * n * n)) / denominator
    return centre - half, centre + half


# --------------------------------------------------------------------------- false discoveries


def benjamini_hochberg(
    tests: Sequence[TestResult], alpha: float = ALPHA
) -> tuple[list[TestResult], float]:
    """q-values for all of a run's tests (one per hypothesis), and the largest p-value that
    passes at ``alpha`` (0 when none does): the run's significance threshold, which the
    sensitivity check re-uses. Order is preserved."""
    m = len(tests)
    if m == 0:
        return [], 0.0
    order = sorted(range(m), key=lambda i: tests[i].p_value)
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, tests[i].p_value * m / rank)
        q[i] = min(1.0, running)
    threshold = 0.0
    for rank, i in enumerate(order, start=1):
        if tests[i].p_value <= alpha * rank / m:
            threshold = tests[i].p_value
    return [replace(t, q_value=q[i]) for i, t in enumerate(tests)], threshold


# --------------------------------------------------------------------------- signal gate


def mutual_information(x: IntArray, y: IntArray) -> float:
    """Mutual information (nats) of two coded variables with the Miller-Madow bias
    correction, which removes most of the upward bias of the plug-in estimate."""
    n = len(x)
    if n == 0:
        return 0.0
    table = _crosstab(x, y)
    joint = table[table > 0] / n
    px = table.sum(axis=1) / n
    py = table.sum(axis=0) / n
    plug_in = float(
        np.sum(joint * np.log(joint)) - np.sum(px * np.log(px)) - np.sum(py * np.log(py))
    )
    kx, ky, kxy = int(np.sum(px > 0)), int(np.sum(py > 0)), int(np.sum(table > 0))
    # H(X) + H(Y) - H(X,Y), each corrected by (k - 1) / 2n.
    correction = ((kx - 1) + (ky - 1) - (kxy - 1)) / (2 * n)
    return plug_in + correction


def signal_gate(
    x: IntArray, y: IntArray, rng: np.random.Generator, shuffles: int = 19
) -> tuple[float, float, bool]:
    """(MI, the largest MI of ``shuffles`` shuffled copies of the target, kept). A candidate
    is kept only if it beats every shuffle: with 19, a feature with no signal passes one time
    in twenty."""
    observed = mutual_information(x, y)
    best = max(mutual_information(x, rng.permutation(y)) for _ in range(shuffles))
    return observed, best, observed > best and observed > 0


def codes(values: Sequence[object] | NDArray[np.generic], *, bins: int = 10) -> IntArray:
    """Code a variable for the gate: numbers into (at most) ``bins`` equal-count bins,
    anything else by its distinct values."""
    array = np.asarray(values)
    if array.dtype.kind in "fiub" and len(np.unique(array)) > bins:
        edges = np.unique(np.quantile(array.astype(float), np.linspace(0, 1, bins + 1)[1:-1]))
        return np.searchsorted(edges, array.astype(float), side="right").astype(np.int64)
    _, inverse = np.unique(array.astype(str), return_inverse=True)
    return inverse.astype(np.int64)

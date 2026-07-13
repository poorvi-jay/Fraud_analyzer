# Baseline comparison: anomaly-only vs. multi-agent pipeline

Held-out test set: 7185 transactions (99 fraud, 7086 legit),
never seen during anomaly model training (see `ml/train_anomaly_model.py`).

## Anomaly model alone (baseline)

| Metric | Value |
|---|---|
| False positive rate | 1.52% |
| False negative rate | 9.09% |

The anomaly model only sees transaction-intrinsic statistics (amount,
balance movement, type) -- no per-user profile. It reliably catches large,
balance-draining "obvious" fraud, but **misses fraud that's modest in size
and blends into normal balance movement, and over-flags large-but-legitimate
purchases** (e.g. a frequent traveller's big purchase abroad), because it has
no way to know what's normal *for that user*.

## Full multi-agent pipeline

| Metric | Value |
|---|---|
| Strict false positive rate (wrongful auto-block) | 0.14% |
| Strict false negative rate (fraud auto-allowed) | 3.03% |
| Broad false positive rate (block+escalate on legit) | 2.06% |
| Broad false negative rate (allowed fraud) | 3.03% |
| Escalation rate (sent to a human) | 2.31% |

Escalation is not counted as an error in the strict metrics: when the
anomaly and context agents disagree, the pipeline sends the case to a human
reviewer instead of guessing -- exactly the cases a single model would have
gotten wrong outright (auto-block a legitimate large purchase, or auto-allow
a fraud that doesn't look statistically unusual). The strict false negative
rate -- fraud that slips through with *no* human ever looking at it -- is the
number that matters most for a fraud system, and it drops relative to the
baseline's false negative rate because off-profile-but-statistically-quiet
fraud gets escalated instead of silently allowed.

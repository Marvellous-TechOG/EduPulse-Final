from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models_dynamic"

RISK_PATH = MODEL_DIR / "risk_model.pkl"
FINAL_CGPA_PATH = MODEL_DIR / "final_cgpa_model.pkl"
NEXT_GPA_PATH = MODEL_DIR / "next_gpa_model.pkl"
META_PATH = MODEL_DIR / "model_meta.json"


def model_ready():
    return all(p.exists() for p in [RISK_PATH, FINAL_CGPA_PATH, META_PATH])


def load_artifacts():
    if not model_ready():
        return None
    with META_PATH.open("r", encoding="utf-8") as f:
        meta = json.load(f)
    return {
        "risk": joblib.load(RISK_PATH),
        "final": joblib.load(FINAL_CGPA_PATH),
        "next": joblib.load(NEXT_GPA_PATH) if NEXT_GPA_PATH.exists() else None,
        "meta": meta,
    }


def calculate_cgpa(records):
    if not records:
        return None
    weighted = []
    weights = []
    for r in records:
        gpa = r.get("gpa")
        if gpa is None:
            continue
        credit = r.get("credits_registered")
        if credit is not None and float(credit) > 0:
            weighted.append(float(gpa) * float(credit))
            weights.append(float(credit))
    if weights and sum(weights) > 0:
        return sum(weighted) / sum(weights)
    vals = [float(r["gpa"]) for r in records if r.get("gpa") is not None]
    return float(np.mean(vals)) if vals else None


def _trend(values):
    if len(values) < 2:
        return 0.0
    x = np.arange(len(values), dtype=float)
    return float(np.polyfit(x, np.array(values, dtype=float), 1)[0])


def build_features(records, programme, school_scale, programme_duration):
    values = [float(r["gpa"]) for r in records if r.get("gpa") is not None]
    if not values:
        return None

    scale = float(school_scale)
    norm = np.array(values, dtype=float) / scale
    current_cgpa = calculate_cgpa(records)
    expected_semesters = max(2, int(programme_duration) * 2)

    row = {
        "programme": str(programme or "Unknown"),
        "latest_gpa_norm": float(norm[-1]),
        "mean_gpa_norm": float(norm.mean()),
        "current_cgpa_norm": float(current_cgpa / scale),
        "min_gpa_norm": float(norm.min()),
        "max_gpa_norm": float(norm.max()),
        "trend_norm": _trend(norm.tolist()),
        "volatility_norm": float(norm.std(ddof=0)),
        "change_norm": float(norm[-1] - norm[0]) if len(norm) > 1 else 0.0,
        "progress_ratio": float(min(1.0, len(norm) / expected_semesters)),
    }
    return pd.DataFrame([row])


def trajectory_summary(records, school_scale):
    """Summarise a student's longitudinal academic pattern in plain language."""
    vals = [float(r["gpa"]) for r in records if r.get("gpa") is not None]
    if not vals:
        return {
            "direction": "BASELINE",
            "message": "No official semester result has been recorded yet.",
            "earliest_gpa": None,
            "latest_gpa": None,
            "change": None,
        }
    change = vals[-1] - vals[0]
    slope = _trend(vals)
    threshold = max(0.12, float(school_scale) * 0.03)
    if slope > threshold / max(1, len(vals) - 1):
        direction = "IMPROVING"
        message = "The student's results are generally improving across the recorded academic history."
    elif slope < -threshold / max(1, len(vals) - 1):
        direction = "DECLINING"
        message = "The student's results are generally declining across the recorded academic history."
    else:
        direction = "STABLE"
        message = "The student's results are broadly stable across the recorded academic history."
    return {
        "direction": direction,
        "message": message,
        "earliest_gpa": vals[0],
        "latest_gpa": vals[-1],
        "change": change,
    }


def risk_band(prob, meta):
    bands = meta.get("risk_band_cutoffs", {})
    moderate = float(bands.get("moderate", 0.30))
    high = float(bands.get("high", 0.55))
    critical = float(bands.get("critical", 0.80))
    if prob >= critical:
        return "CRITICAL"
    if prob >= high:
        return "HIGH"
    if prob >= moderate:
        return "MODERATE"
    return "LOW"


def explain_model(features, risk_model, meta, base_prob):
    """Return plain-language evidence suitable for school personnel."""
    row = features.iloc[0]
    factors = []

    latest = float(row["latest_gpa_norm"])
    mean = float(row["mean_gpa_norm"])
    trend = float(row["trend_norm"])
    volatility = float(row["volatility_norm"])
    change = float(row["change_norm"])

    if trend <= -0.035:
        factors.append("The student's GPA has shown a clear downward pattern across the recorded academic periods.")
    elif trend >= 0.035:
        factors.append("The student's GPA has been improving across the recorded academic periods.")

    if latest < mean - 0.05:
        factors.append("The latest semester GPA is noticeably below the student's own average performance.")
    elif latest > mean + 0.05:
        factors.append("The latest semester GPA is above the student's own historical average.")

    if volatility >= 0.09:
        factors.append("Performance has changed considerably from one semester to another, so the student's results are not yet stable.")

    if change <= -0.10:
        factors.append("The most recent performance is substantially lower than the earliest recorded performance.")
    elif change >= 0.10:
        factors.append("The student has made substantial progress compared with the earliest recorded performance.")

    medians = meta.get("feature_medians", {})
    labels = {
        "latest_gpa_norm": "recent semester performance",
        "mean_gpa_norm": "overall academic history",
        "current_cgpa_norm": "current cumulative performance",
        "min_gpa_norm": "the student's weakest recorded semester",
        "trend_norm": "the direction of academic performance",
        "volatility_norm": "semester-to-semester consistency",
        "change_norm": "change between the earliest and latest result",
        "progress_ratio": "how far the student has progressed through the course",
    }
    contributions = []
    for feature, median in medians.items():
        if feature not in features.columns:
            continue
        modified = features.copy()
        modified.loc[0, feature] = float(median)
        try:
            alt_prob = float(risk_model.predict_proba(modified)[:, 1][0])
            contributions.append((base_prob - alt_prob, feature))
        except Exception:
            pass

    for delta, feature in sorted(contributions, reverse=True):
        if delta > 0.04:
            factors.append(
                f"The model also gives meaningful weight to {labels.get(feature, feature.replace('_',' '))} "
                "when estimating this student's academic risk."
            )
            break

    if not factors:
        factors.append("No single issue dominates the prediction. EduPulse is combining the student's complete recorded academic history to estimate the risk.")

    return factors[:4]


def recommend_actions(level, factors):
    base = {
        "CRITICAL": [
            "Immediate academic adviser review.",
            "Create a structured intervention plan with a short review interval.",
            "Review weak courses, attendance and outstanding academic obligations.",
        ],
        "HIGH": [
            "Priority academic adviser consultation.",
            "Targeted tutorial or remedial support in weak courses.",
            "Review progress again after the next assessment checkpoint.",
        ],
        "MODERATE": [
            "Place the student under enhanced monitoring.",
            "Discuss recent performance changes with the student.",
            "Review again after the next continuous assessment or semester result.",
        ],
        "LOW": [
            "Continue routine academic monitoring.",
            "Retain the prediction as a baseline for the next automatic refresh.",
        ],
    }[level]
    if any("trajectory" in f.lower() for f in factors):
        base.append("Investigate the reason for the observed downward academic trajectory.")
    return base


def predict_student(records, student, school_scale):
    artifacts = load_artifacts()
    if artifacts is None:
        return {
            "available": False,
            "reason": "Dynamic model artifacts are not available. Run train_dynamic_models.py first."
        }
    if not records:
        return {
            "available": False,
            "reason": "No semester GPA has been recorded yet. Fresher/baseline monitoring remains active until the first academic result."
        }

    features = build_features(
        records,
        student.get("programme"),
        school_scale,
        student.get("programme_duration", 4),
    )
    risk_model = artifacts["risk"]
    final_model = artifacts["final"]
    next_model = artifacts["next"]
    meta = artifacts["meta"]

    prob = float(risk_model.predict_proba(features)[:, 1][0])
    final_norm = float(final_model.predict(features)[0])
    next_norm = float(next_model.predict(features)[0]) if next_model is not None else None

    final_cgpa = float(np.clip(final_norm * school_scale, 0, school_scale))
    next_gpa = (
        float(np.clip(next_norm * school_scale, 0, school_scale))
        if next_norm is not None else None
    )
    level = risk_band(prob, meta)
    factors = explain_model(features, risk_model, meta, prob)

    return {
        "available": True,
        "model_version": meta.get("model_version", "dynamic-v1"),
        "predicted_next_gpa": next_gpa,
        "predicted_final_cgpa": final_cgpa,
        "risk_probability": prob,
        "risk_level": level,
        "factors": factors,
        "recommendations": recommend_actions(level, factors),
        "feature_snapshot": features.iloc[0].to_dict(),
        "current_cgpa": calculate_cgpa(records),
    }

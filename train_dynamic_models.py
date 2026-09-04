from __future__ import annotations

import json
import re
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor, RandomForestClassifier, RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, f1_score, precision_score, recall_score, roc_auc_score,
    mean_absolute_error, mean_squared_error, r2_score, precision_recall_curve,
)
from sklearn.model_selection import GroupShuffleSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data" / "nigeria_student_performance.xlsx"
OUTPUT_DIR = BASE_DIR / "models_dynamic"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

FEATURES = [
    "programme",
    "latest_gpa_norm",
    "mean_gpa_norm",
    "current_cgpa_norm",
    "min_gpa_norm",
    "max_gpa_norm",
    "trend_norm",
    "volatility_norm",
    "change_norm",
    "progress_ratio",
]


def clean_name(x):
    return re.sub(r"[^a-z0-9]+", "", str(x).strip().lower())


def find_column(columns, candidates):
    normalized = {clean_name(c): c for c in columns}
    for candidate in candidates:
        key = clean_name(candidate)
        if key in normalized:
            return normalized[key]
    for c in columns:
        nc = clean_name(c)
        for candidate in candidates:
            tokens = [t for t in re.split(r"[^a-z0-9]+", candidate.lower()) if t]
            if tokens and all(t in nc for t in tokens):
                return c
    return None


def load_data(path=DATA_PATH):
    if not path.exists():
        raise FileNotFoundError(f"{path} not found.")
    sheets = pd.read_excel(path, sheet_name=None)
    frames = []

    for sheet_name, raw in sheets.items():
        if raw.empty:
            continue
        cols = list(raw.columns)
        mapping = {
            "programme": find_column(cols, ["programme","program","course of study","department"]),
            "gpa_100": find_column(cols, ["100 level gpa","100l gpa","first year gpa","year 1 gpa","gpa 100"]),
            "gpa_200": find_column(cols, ["200 level gpa","200l gpa","second year gpa","year 2 gpa","gpa 200"]),
            "gpa_300": find_column(cols, ["300 level gpa","300l gpa","third year gpa","year 3 gpa","gpa 300"]),
            "final_cgpa": find_column(cols, ["final cgpa","overall cgpa","graduation cgpa","final cumulative gpa"]),
        }
        if mapping["final_cgpa"] is None:
            for c in cols:
                if clean_name(c) == "cgpa":
                    mapping["final_cgpa"] = c
                    break
        if mapping["final_cgpa"] is None:
            continue
        rename = {v:k for k,v in mapping.items() if v is not None}
        df = raw.rename(columns=rename).copy()
        if "programme" not in df.columns:
            df["programme"] = str(sheet_name)
        for c in ["gpa_100","gpa_200","gpa_300","final_cgpa"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        frames.append(df)

    if not frames:
        raise ValueError("No usable Nigerian academic sheet with final CGPA was found.")

    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["final_cgpa"]).reset_index(drop=True)

    max_seen = np.nanmax(
        np.concatenate([
            df[c].dropna().values
            for c in ["gpa_100","gpa_200","gpa_300","final_cgpa"]
            if c in df.columns
        ])
    )
    training_scale = 4.0 if max_seen <= 4.05 else 5.0
    return df, training_scale


def history_features(values, programme, progress, scale):
    norm = np.array(values, dtype=float) / float(scale)
    trend = 0.0
    if len(norm) >= 2:
        trend = float(np.polyfit(np.arange(len(norm)), norm, 1)[0])
    return {
        "programme": str(programme or "Unknown"),
        "latest_gpa_norm": float(norm[-1]),
        "mean_gpa_norm": float(norm.mean()),
        "current_cgpa_norm": float(norm.mean()),
        "min_gpa_norm": float(norm.min()),
        "max_gpa_norm": float(norm.max()),
        "trend_norm": trend,
        "volatility_norm": float(norm.std(ddof=0)),
        "change_norm": float(norm[-1] - norm[0]) if len(norm) > 1 else 0.0,
        "progress_ratio": float(progress),
    }


def make_snapshots(df, scale):
    final_rows = []
    next_rows = []
    for group_id, row in df.iterrows():
        programme = row.get("programme", "Unknown")
        history = [
            float(row[c]) for c in ["gpa_100","gpa_200","gpa_300"]
            if c in row and pd.notna(row[c])
        ]
        if not history:
            continue

        final_norm = float(row["final_cgpa"]) / scale
        for k in range(1, len(history) + 1):
            progress = k / 4.0
            features = history_features(history[:k], programme, progress, scale)
            final_rows.append({
                **features,
                "target_final_norm": final_norm,
                "group_id": group_id,
            })
            if k < len(history):
                next_rows.append({
                    **features,
                    "target_next_norm": history[k] / scale,
                    "group_id": group_id,
                })

    final_df = pd.DataFrame(final_rows)
    next_df = pd.DataFrame(next_rows)
    low_cutoff = float(df["final_cgpa"].quantile(0.25)) / scale
    final_df["target_low"] = (final_df["target_final_norm"] <= low_cutoff).astype(int)
    return final_df, next_df, low_cutoff


def preprocessor(frame):
    numeric = [f for f in FEATURES if f != "programme"]
    return ColumnTransformer([
        ("num", Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]), numeric),
        ("cat", Pipeline([
            ("imputer", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore")),
        ]), ["programme"]),
    ])


def split_by_group(frame):
    splitter = GroupShuffleSplit(n_splits=1, test_size=0.20, random_state=42)
    tr, te = next(splitter.split(frame, groups=frame["group_id"]))
    return frame.iloc[tr].copy(), frame.iloc[te].copy()


def choose_threshold(y, prob):
    precision, recall, thresholds = precision_recall_curve(y, prob)
    if len(thresholds) == 0:
        return 0.5
    f1 = 2 * precision[:-1] * recall[:-1] / (precision[:-1] + recall[:-1] + 1e-12)
    return float(thresholds[int(np.argmax(f1))])


def classifier_metrics(y, prob, threshold):
    pred = (prob >= threshold).astype(int)
    return {
        "accuracy": float(accuracy_score(y, pred)),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "roc_auc": float(roc_auc_score(y, prob)),
    }


def regression_metrics(y, pred):
    return {
        "mae": float(mean_absolute_error(y, pred)),
        "rmse": float(mean_squared_error(y, pred) ** 0.5),
        "r2": float(r2_score(y, pred)),
    }


def main():
    df, scale = load_data()
    final_df, next_df, low_cutoff_norm = make_snapshots(df, scale)

    train, test = split_by_group(final_df)
    Xtr, Xte = train[FEATURES], test[FEATURES]
    ytr, yte = train["target_low"], test["target_low"]

    classifier_candidates = {
        "Logistic Regression": LogisticRegression(max_iter=2500, class_weight="balanced"),
        "Random Forest": RandomForestClassifier(
            n_estimators=280, max_depth=10, min_samples_leaf=3,
            class_weight="balanced", random_state=42, n_jobs=1
        ),
        "Gradient Boosting": GradientBoostingClassifier(
            n_estimators=160, learning_rate=0.05, max_depth=2, random_state=42
        ),
    }

    classifier_results = {}
    best_classifier = None
    best_classifier_name = None
    best_threshold = 0.5
    best_f1 = -1

    for name, estimator in classifier_candidates.items():
        pipe = Pipeline([("prep", preprocessor(train)), ("model", estimator)])
        pipe.fit(Xtr, ytr)
        prob = pipe.predict_proba(Xte)[:, 1]
        threshold = choose_threshold(yte.values, prob)
        metrics = classifier_metrics(yte.values, prob, threshold)
        classifier_results[name] = {**metrics, "threshold": threshold}
        if metrics["f1"] > best_f1:
            best_f1 = metrics["f1"]
            best_classifier = pipe
            best_classifier_name = name
            best_threshold = threshold

    # Final-CGPA regression from any available history depth.
    ytr_r, yte_r = train["target_final_norm"], test["target_final_norm"]
    regression_candidates = {
        "Random Forest Regressor": RandomForestRegressor(
            n_estimators=300, max_depth=12, min_samples_leaf=2,
            random_state=42, n_jobs=1
        ),
        "Gradient Boosting Regressor": GradientBoostingRegressor(
            n_estimators=180, learning_rate=0.04, max_depth=2, random_state=42
        ),
    }
    regression_results = {}
    best_final = None
    best_final_name = None
    best_rmse = 999
    for name, estimator in regression_candidates.items():
        pipe = Pipeline([("prep", preprocessor(train)), ("model", estimator)])
        pipe.fit(Xtr, ytr_r)
        pred = pipe.predict(Xte)
        metrics = regression_metrics(yte_r, pred)
        regression_results[name] = metrics
        if metrics["rmse"] < best_rmse:
            best_rmse = metrics["rmse"]
            best_final = pipe
            best_final_name = name

    # Next-period GPA model.
    best_next = None
    next_results = {}
    if not next_df.empty:
        ntrain, ntest = split_by_group(next_df)
        Xntr, Xnte = ntrain[FEATURES], ntest[FEATURES]
        yntr, ynte = ntrain["target_next_norm"], ntest["target_next_norm"]
        best_next_rmse = 999
        for name, estimator in regression_candidates.items():
            pipe = Pipeline([("prep", preprocessor(ntrain)), ("model", estimator)])
            pipe.fit(Xntr, yntr)
            pred = pipe.predict(Xnte)
            metrics = regression_metrics(ynte, pred)
            next_results[name] = metrics
            if metrics["rmse"] < best_next_rmse:
                best_next_rmse = metrics["rmse"]
                best_next = pipe

    # Model-derived display bands from held-out probabilities.
    heldout_prob = best_classifier.predict_proba(Xte)[:, 1]
    moderate = float(np.quantile(heldout_prob, 0.50))
    high = max(float(np.quantile(heldout_prob, 0.75)), float(best_threshold))
    critical = max(float(np.quantile(heldout_prob, 0.90)), high + 0.05)
    critical = min(0.98, critical)
    if critical <= high:
        critical = min(0.98, high + 0.08)
    if high <= moderate:
        high = min(0.90, moderate + 0.08)

    feature_medians = {
        f: float(final_df[f].median())
        for f in FEATURES if f != "programme"
    }

    joblib.dump(best_classifier, OUTPUT_DIR / "risk_model.pkl")
    joblib.dump(best_final, OUTPUT_DIR / "final_cgpa_model.pkl")
    if best_next is not None:
        joblib.dump(best_next, OUTPUT_DIR / "next_gpa_model.pkl")

    meta = {
        "model_version": "EduPulse-Dynamic-v2",
        "training_scale": scale,
        "training_records": int(len(df)),
        "training_snapshots": int(len(final_df)),
        "features": FEATURES,
        "target_definition": (
            "Classifier predicts whether the known future final CGPA belongs to the "
            "lowest quartile of historical final outcomes. Regression predicts future final CGPA. "
            "A separate regression model predicts the next academic checkpoint GPA."
        ),
        "low_performance_cutoff_normalized": low_cutoff_norm,
        "selected_classifier": best_classifier_name,
        "classifier_results": classifier_results,
        "selected_final_regressor": best_final_name,
        "final_regression_results": regression_results,
        "next_gpa_results": next_results,
        "selected_probability_threshold": best_threshold,
        "risk_band_cutoffs": {
            "moderate": moderate,
            "high": high,
            "critical": critical,
        },
        "feature_medians": feature_medians,
        "important_note": (
            "The source dataset contains level-level GPA checkpoints. The operational app accepts "
            "semester GPA histories and normalises them to the institution's 4.0 or 5.0 scale. "
            "For production adoption, retrain this model on the target institution's semester-level records."
        ),
    }
    with (OUTPUT_DIR / "model_meta.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print("Dynamic EduPulse models trained successfully.")
    print("Selected classifier:", best_classifier_name)
    print("Selected final-CGPA regressor:", best_final_name)
    print("Model artifacts:", OUTPUT_DIR)
    print(json.dumps(meta["risk_band_cutoffs"], indent=2))


if __name__ == "__main__":
    main()
